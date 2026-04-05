package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ============================================================================
// Collecteur de données prix-carburants France
// Source : data.economie.gouv.fr — Flux instantané v2
// Licence Ouverte / Open Licence
//
// Usage :
//   go run main.go                     → export JSON + CSV dans ./data/
//   go run main.go -format json        → JSON uniquement
//   go run main.go -format csv         → CSV uniquement
//   go run main.go -format sqlite      → SQLite (nécessite CGO)
//   go run main.go -out /chemin/vers/  → répertoire de sortie personnalisé
//
// Planification cron (toutes les heures) :
//   0 * * * * cd /chemin/vers/projet && go run main.go >> /var/log/carburants.log 2>&1
// ============================================================================

const (
	baseURL  = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/prix-des-carburants-en-france-flux-instantane-v2/records"
	pageSize = 100 // max autorisé par l'API
)

// --- Structures de données ---------------------------------------------------

// Station représente un point de vente avec tous ses carburants
type Station struct {
	// Identité
	ID         int     `json:"id"`
	Adresse    string  `json:"adresse"`
	Ville      string  `json:"ville"`
	CP         string  `json:"cp"`
	Departement    string  `json:"departement"`
	CodeDepartement string `json:"code_departement"`
	Region         string  `json:"region"`
	CodeRegion     string  `json:"code_region"`
	Latitude   float64 `json:"latitude"`
	Longitude  float64 `json:"longitude"`
	Pop        string  `json:"pop"` // R=route, A=autoroute

	// Horaires
	Automate2424 bool `json:"automate_24_24"`

	// Services
	Services []string `json:"services"`

	// Prix par carburant (nil = non proposé)
	Gazole *Carburant `json:"gazole,omitempty"`
	SP95   *Carburant `json:"sp95,omitempty"`
	SP98   *Carburant `json:"sp98,omitempty"`
	E10    *Carburant `json:"e10,omitempty"`
	E85    *Carburant `json:"e85,omitempty"`
	GPLc   *Carburant `json:"gplc,omitempty"`

	// Listes pratiques
	CarburantsDisponibles   []string `json:"carburants_disponibles"`
	CarburantsIndisponibles []string `json:"carburants_indisponibles"`
	RuptureTemporaire       []string `json:"rupture_temporaire,omitempty"`
	RuptureDefinitive       []string `json:"rupture_definitive,omitempty"`
}

// Carburant contient le prix et l'état d'approvisionnement
type Carburant struct {
	Prix         *float64 `json:"prix,omitempty"`          // €/L — nil si pas de prix
	MajPrix      string   `json:"maj_prix,omitempty"`      // date de dernière MAJ du prix
	RuptureType  string   `json:"rupture_type,omitempty"`  // "", "temporaire", "definitive"
	RuptureDebut string   `json:"rupture_debut,omitempty"` // date début de rupture
}

// Export final
type Export struct {
	MetaDonnees MetaDonnees `json:"meta"`
	Stations    []Station   `json:"stations"`
}

type MetaDonnees struct {
	Source        string `json:"source"`
	DateCollecte  string `json:"date_collecte"`
	NombreStations int   `json:"nombre_stations"`
	DureeCollecte  string `json:"duree_collecte"`
}

// --- Structures brutes de l'API -----------------------------------------------

type apiResponse struct {
	TotalCount int         `json:"total_count"`
	Results    []apiRecord `json:"results"`
}

type apiGeom struct {
	Lon float64 `json:"lon"`
	Lat float64 `json:"lat"`
}

type apiRecord struct {
	ID      int      `json:"id"`
	CP      string   `json:"cp"`
	Pop     string   `json:"pop"`
	Adresse string   `json:"adresse"`
	Ville   string   `json:"ville"`
	Geom    *apiGeom `json:"geom"`

	Departement     string `json:"departement"`
	CodeDepartement string `json:"code_departement"`
	Region          string `json:"region"`
	CodeRegion      string `json:"code_region"`

	HorairesAutomate string `json:"horaires_automate_24_24"`
	ServicesService  []string `json:"services_service"`

	// Prix
	GazolePrix *float64 `json:"gazole_prix"`
	GazoleMaj  *string  `json:"gazole_maj"`
	SP95Prix   *float64 `json:"sp95_prix"`
	SP95Maj    *string  `json:"sp95_maj"`
	SP98Prix   *float64 `json:"sp98_prix"`
	SP98Maj    *string  `json:"sp98_maj"`
	E10Prix    *float64 `json:"e10_prix"`
	E10Maj     *string  `json:"e10_maj"`
	E85Prix    *float64 `json:"e85_prix"`
	E85Maj     *string  `json:"e85_maj"`
	GPLcPrix   *float64 `json:"gplc_prix"`
	GPLcMaj    *string  `json:"gplc_maj"`

	// Ruptures
	GazoleRuptureType  *string `json:"gazole_rupture_type"`
	GazoleRuptureDebut *string `json:"gazole_rupture_debut"`
	SP95RuptureType    *string `json:"sp95_rupture_type"`
	SP95RuptureDebut   *string `json:"sp95_rupture_debut"`
	SP98RuptureType    *string `json:"sp98_rupture_type"`
	SP98RuptureDebut   *string `json:"sp98_rupture_debut"`
	E10RuptureType     *string `json:"e10_rupture_type"`
	E10RuptureDebut    *string `json:"e10_rupture_debut"`
	E85RuptureType     *string `json:"e85_rupture_type"`
	E85RuptureDebut    *string `json:"e85_rupture_debut"`
	GPLcRuptureType    *string `json:"gplc_rupture_type"`
	GPLcRuptureDebut   *string `json:"gplc_rupture_debut"`

	CarburantsDisponibles      []string `json:"carburants_disponibles"`
	CarburantsIndisponibles    []string `json:"carburants_indisponibles"`
	CarburantsRuptureTemporaire *string `json:"carburants_rupture_temporaire"`
	CarburantsRuptureDefinitive *string `json:"carburants_rupture_definitive"`
}

// --- Logique principale -------------------------------------------------------

func main() {
	// Parse des arguments simples
	outputDir := "./data"
	format := "all" // "json", "csv", "all"

	for i := 1; i < len(os.Args); i++ {
		switch os.Args[i] {
		case "-out":
			if i+1 < len(os.Args) {
				outputDir = os.Args[i+1]
				i++
			}
		case "-format":
			if i+1 < len(os.Args) {
				format = os.Args[i+1]
				i++
			}
		case "-help", "--help", "-h":
			fmt.Println("Usage: go run main.go [-format json|csv|all] [-out répertoire]")
			fmt.Println("  -format  : format de sortie (défaut: all = JSON + CSV)")
			fmt.Println("  -out     : répertoire de sortie (défaut: ./data)")
			os.Exit(0)
		}
	}

	os.MkdirAll(outputDir, 0755)

	start := time.Now()
	log.Printf("🚗 Démarrage de la collecte des stations-service françaises...")

	// 1. Récupérer toutes les stations
	stations, total, err := fetchAllStations()
	if err != nil {
		log.Fatalf("❌ Erreur lors de la collecte : %v", err)
	}

	duree := time.Since(start)
	log.Printf("✅ %d stations collectées sur %d en %s", len(stations), total, duree.Round(time.Millisecond))

	// 2. Construire l'export
	export := Export{
		MetaDonnees: MetaDonnees{
			Source:         "data.economie.gouv.fr — Flux instantané v2",
			DateCollecte:   time.Now().Format(time.RFC3339),
			NombreStations: len(stations),
			DureeCollecte:  duree.Round(time.Millisecond).String(),
		},
		Stations: stations,
	}

	// 3. Sauvegarder
	ts := time.Now().Format("2006-01-02_15h04")

	if format == "json" || format == "all" {
		jsonPath := filepath.Join(outputDir, fmt.Sprintf("stations_%s.json", ts))
		if err := saveJSON(export, jsonPath); err != nil {
			log.Fatalf("❌ Erreur JSON : %v", err)
		}
		// Lien symbolique "latest"
		latestJSON := filepath.Join(outputDir, "stations_latest.json")
		os.Remove(latestJSON)
		os.Symlink(filepath.Base(jsonPath), latestJSON)
		log.Printf("📄 JSON → %s", jsonPath)
	}

	if format == "csv" || format == "all" {
		csvPath := filepath.Join(outputDir, fmt.Sprintf("stations_%s.csv", ts))
		if err := saveCSV(stations, csvPath); err != nil {
			log.Fatalf("❌ Erreur CSV : %v", err)
		}
		latestCSV := filepath.Join(outputDir, "stations_latest.csv")
		os.Remove(latestCSV)
		os.Symlink(filepath.Base(csvPath), latestCSV)
		log.Printf("📄 CSV → %s", csvPath)
	}

	// 4. Stats rapides
	printStats(stations)
}

// fetchAllStations pagine à travers l'API et retourne toutes les stations
func fetchAllStations() ([]Station, int, error) {
	client := &http.Client{Timeout: 30 * time.Second}

	// Premier appel pour connaître le total
	firstResp, err := fetchPage(client, 0)
	if err != nil {
		return nil, 0, fmt.Errorf("première requête échouée: %w", err)
	}

	total := firstResp.TotalCount
	log.Printf("📊 %d stations à récupérer (pages de %d)", total, pageSize)

	stations := make([]Station, 0, total)
	for _, r := range firstResp.Results {
		stations = append(stations, convertRecord(r))
	}

	// Pages suivantes
	for offset := pageSize; offset < total; offset += pageSize {
		resp, err := fetchPage(client, offset)
		if err != nil {
			log.Printf("⚠️  Erreur page offset=%d, retry...", offset)
			time.Sleep(2 * time.Second)
			resp, err = fetchPage(client, offset)
			if err != nil {
				return nil, total, fmt.Errorf("échec offset=%d après retry: %w", offset, err)
			}
		}
		for _, r := range resp.Results {
			stations = append(stations, convertRecord(r))
		}

		// Log de progression toutes les 1000 stations
		if (offset/pageSize)%10 == 0 {
			log.Printf("   ... %d/%d stations", len(stations), total)
		}

		// Politesse envers l'API
		time.Sleep(100 * time.Millisecond)
	}

	return stations, total, nil
}

func fetchPage(client *http.Client, offset int) (*apiResponse, error) {
	url := fmt.Sprintf("%s?limit=%d&offset=%d", baseURL, pageSize, offset)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "CarburantsFR-Collector/1.0")

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("requête HTTP échouée: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(body[:min(200, len(body))]))
	}

	var result apiResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("décodage JSON échoué: %w", err)
	}

	return &result, nil
}

// convertRecord transforme un enregistrement brut de l'API en Station propre
func convertRecord(r apiRecord) Station {
	s := Station{
		ID:              r.ID,
		Adresse:         r.Adresse,
		Ville:           r.Ville,
		CP:              r.CP,
		Departement:     r.Departement,
		CodeDepartement: r.CodeDepartement,
		Region:          r.Region,
		CodeRegion:      r.CodeRegion,
		Pop:             r.Pop,
		Automate2424:    r.HorairesAutomate == "Oui",
		Services:        r.ServicesService,
		CarburantsDisponibles:   r.CarburantsDisponibles,
		CarburantsIndisponibles: r.CarburantsIndisponibles,
	}

	if r.Geom != nil {
		s.Latitude = r.Geom.Lat
		s.Longitude = r.Geom.Lon
	}

	// Carburants — on crée l'entrée si prix OU rupture existe
	s.Gazole = buildCarburant(r.GazolePrix, r.GazoleMaj, r.GazoleRuptureType, r.GazoleRuptureDebut)
	s.SP95 = buildCarburant(r.SP95Prix, r.SP95Maj, r.SP95RuptureType, r.SP95RuptureDebut)
	s.SP98 = buildCarburant(r.SP98Prix, r.SP98Maj, r.SP98RuptureType, r.SP98RuptureDebut)
	s.E10 = buildCarburant(r.E10Prix, r.E10Maj, r.E10RuptureType, r.E10RuptureDebut)
	s.E85 = buildCarburant(r.E85Prix, r.E85Maj, r.E85RuptureType, r.E85RuptureDebut)
	s.GPLc = buildCarburant(r.GPLcPrix, r.GPLcMaj, r.GPLcRuptureType, r.GPLcRuptureDebut)

	// Ruptures agrégées
	if r.CarburantsRuptureTemporaire != nil && *r.CarburantsRuptureTemporaire != "" {
		s.RuptureTemporaire = strings.Split(*r.CarburantsRuptureTemporaire, ", ")
	}
	if r.CarburantsRuptureDefinitive != nil && *r.CarburantsRuptureDefinitive != "" {
		s.RuptureDefinitive = strings.Split(*r.CarburantsRuptureDefinitive, ", ")
	}

	return s
}

func buildCarburant(prix *float64, maj *string, ruptType *string, ruptDebut *string) *Carburant {
	hasPrix := prix != nil
	hasRupture := ruptType != nil && *ruptType != ""

	if !hasPrix && !hasRupture {
		return nil
	}

	c := &Carburant{}
	if hasPrix {
		c.Prix = prix
	}
	if maj != nil {
		c.MajPrix = *maj
	}
	if hasRupture {
		c.RuptureType = *ruptType
	}
	if ruptDebut != nil {
		c.RuptureDebut = *ruptDebut
	}
	return c
}

// --- Exports -----------------------------------------------------------------

func saveJSON(export Export, path string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	return enc.Encode(export)
}

func saveCSV(stations []Station, path string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	// En-tête
	header := strings.Join([]string{
		"id", "adresse", "ville", "cp", "departement", "code_departement",
		"region", "code_region", "latitude", "longitude", "pop", "automate_24_24",
		"gazole_prix", "gazole_maj", "gazole_rupture",
		"sp95_prix", "sp95_maj", "sp95_rupture",
		"sp98_prix", "sp98_maj", "sp98_rupture",
		"e10_prix", "e10_maj", "e10_rupture",
		"e85_prix", "e85_maj", "e85_rupture",
		"gplc_prix", "gplc_maj", "gplc_rupture",
		"carburants_disponibles", "carburants_indisponibles",
		"rupture_temporaire", "rupture_definitive",
		"services",
	}, ";")
	fmt.Fprintln(f, header)

	for _, s := range stations {
		auto := "Non"
		if s.Automate2424 {
			auto = "Oui"
		}

		row := strings.Join([]string{
			fmt.Sprintf("%d", s.ID),
			csvEscape(s.Adresse),
			csvEscape(s.Ville),
			s.CP,
			csvEscape(s.Departement),
			s.CodeDepartement,
			csvEscape(s.Region),
			s.CodeRegion,
			fmt.Sprintf("%.6f", s.Latitude),
			fmt.Sprintf("%.6f", s.Longitude),
			s.Pop,
			auto,
			carburantPrixCSV(s.Gazole),
			carburantMajCSV(s.Gazole),
			carburantRuptureCSV(s.Gazole),
			carburantPrixCSV(s.SP95),
			carburantMajCSV(s.SP95),
			carburantRuptureCSV(s.SP95),
			carburantPrixCSV(s.SP98),
			carburantMajCSV(s.SP98),
			carburantRuptureCSV(s.SP98),
			carburantPrixCSV(s.E10),
			carburantMajCSV(s.E10),
			carburantRuptureCSV(s.E10),
			carburantPrixCSV(s.E85),
			carburantMajCSV(s.E85),
			carburantRuptureCSV(s.E85),
			carburantPrixCSV(s.GPLc),
			carburantMajCSV(s.GPLc),
			carburantRuptureCSV(s.GPLc),
			csvEscape(strings.Join(s.CarburantsDisponibles, ",")),
			csvEscape(strings.Join(s.CarburantsIndisponibles, ",")),
			csvEscape(strings.Join(s.RuptureTemporaire, ",")),
			csvEscape(strings.Join(s.RuptureDefinitive, ",")),
			csvEscape(strings.Join(s.Services, ",")),
		}, ";")
		fmt.Fprintln(f, row)
	}

	return nil
}

func carburantPrixCSV(c *Carburant) string {
	if c == nil || c.Prix == nil {
		return ""
	}
	return fmt.Sprintf("%.3f", *c.Prix)
}

func carburantMajCSV(c *Carburant) string {
	if c == nil {
		return ""
	}
	return c.MajPrix
}

func carburantRuptureCSV(c *Carburant) string {
	if c == nil || c.RuptureType == "" {
		return ""
	}
	return c.RuptureType
}

func csvEscape(s string) string {
	if strings.ContainsAny(s, ";\"\n") {
		return `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
	}
	return s
}

// --- Stats -------------------------------------------------------------------

func printStats(stations []Station) {
	var (
		total          = len(stations)
		automate       int
		autoroute      int
		ruptTemp       int
		ruptDef        int
		gazoleCount    int
		sp95Count      int
		sp98Count      int
		e10Count       int
		e85Count       int
		gplcCount      int
		gazoleSum      float64
		sp95Sum        float64
		sp98Sum        float64
		e10Sum         float64
		e85Sum         float64
		gplcSum        float64
	)

	for _, s := range stations {
		if s.Automate2424 {
			automate++
		}
		if s.Pop == "A" {
			autoroute++
		}
		if len(s.RuptureTemporaire) > 0 {
			ruptTemp++
		}
		if len(s.RuptureDefinitive) > 0 {
			ruptDef++
		}
		if s.Gazole != nil && s.Gazole.Prix != nil {
			gazoleCount++
			gazoleSum += *s.Gazole.Prix
		}
		if s.SP95 != nil && s.SP95.Prix != nil {
			sp95Count++
			sp95Sum += *s.SP95.Prix
		}
		if s.SP98 != nil && s.SP98.Prix != nil {
			sp98Count++
			sp98Sum += *s.SP98.Prix
		}
		if s.E10 != nil && s.E10.Prix != nil {
			e10Count++
			e10Sum += *s.E10.Prix
		}
		if s.E85 != nil && s.E85.Prix != nil {
			e85Count++
			e85Sum += *s.E85.Prix
		}
		if s.GPLc != nil && s.GPLc.Prix != nil {
			gplcCount++
			gplcSum += *s.GPLc.Prix
		}
	}

	fmt.Println()
	fmt.Println("╔══════════════════════════════════════════════════════════════╗")
	fmt.Println("║           STATIONS-SERVICE FRANCE — RÉSUMÉ                  ║")
	fmt.Println("╠══════════════════════════════════════════════════════════════╣")
	fmt.Printf("║  Total stations        : %-6d                             ║\n", total)
	fmt.Printf("║  Automate 24/24        : %-6d                             ║\n", automate)
	fmt.Printf("║  Autoroute             : %-6d                             ║\n", autoroute)
	fmt.Printf("║  Rupture temporaire    : %-6d stations                    ║\n", ruptTemp)
	fmt.Printf("║  Rupture définitive    : %-6d stations                    ║\n", ruptDef)
	fmt.Println("╠══════════════════════════════════════════════════════════════╣")
	fmt.Println("║  PRIX MOYENS                                                ║")
	if gazoleCount > 0 {
		fmt.Printf("║    Gazole  : %.3f €/L  (%d stations)                     ║\n", gazoleSum/float64(gazoleCount), gazoleCount)
	}
	if sp95Count > 0 {
		fmt.Printf("║    SP95    : %.3f €/L  (%d stations)                     ║\n", sp95Sum/float64(sp95Count), sp95Count)
	}
	if sp98Count > 0 {
		fmt.Printf("║    SP98    : %.3f €/L  (%d stations)                     ║\n", sp98Sum/float64(sp98Count), sp98Count)
	}
	if e10Count > 0 {
		fmt.Printf("║    E10     : %.3f €/L  (%d stations)                     ║\n", e10Sum/float64(e10Count), e10Count)
	}
	if e85Count > 0 {
		fmt.Printf("║    E85     : %.3f €/L  (%d stations)                     ║\n", e85Sum/float64(e85Count), e85Count)
	}
	if gplcCount > 0 {
		fmt.Printf("║    GPLc    : %.3f €/L  (%d stations)                      ║\n", gplcSum/float64(gplcCount), gplcCount)
	}
	fmt.Println("╚══════════════════════════════════════════════════════════════╝")
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
