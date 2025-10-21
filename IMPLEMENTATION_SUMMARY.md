# Implementierung: Zabbix Config Management - Export Funktionalität

## Zusammenfassung

Das Skript `nc_zabbix_config_mgmt.py` wurde erfolgreich erweitert, um Batch-Exporte von Zabbix Dashboards, Maps und Hosts zu unterstützen.

## Implementierte Funktionen

### 1. Neue Befehlszeilen-Argumente

```bash
python nc_zabbix_config_mgmt.py --config <connection_config_path> --export <export_file_path>
```

**Parameter:**
- `--config`: Pfad zur JSON-Konfigurationsdatei mit Zabbix-Verbindungsdaten und zu exportierenden Objekten
- `--export`: Basis-Verzeichnis für die Exportdateien

### 2. Konfigurationsdatei-Format

Die JSON-Konfigurationsdatei enthält:
- `url`: Zabbix API URL
- `username`: Zabbix Benutzername
- `password`: Zabbix Passwort
- `ca_certificate_path`: (Optional) Pfad zum CA-Zertifikat
- `export_format`: Export-Format (json oder yaml)
- `dashboards`: Array von Dashboard-Namen
- `maps`: Array von Map-Namen
- `hosts`: Array von Host-Namen

### 3. Funktionsweise

1. **Konfiguration einlesen**: Liest die JSON-Konfigurationsdatei
2. **Export-Verzeichnis erstellen**: Erstellt `nc_zabbix_export` Unterordner
3. **Objekte exportieren**: 
   - Ruft `export_dashboard.py` für jedes Dashboard auf
   - Ruft `map_export.py` für jede Map auf
   - Ruft `host_export.py` für jeden Host auf
4. **Fehlerbehandlung**: Loggt Fehler, bricht aber nicht ab
5. **Zusammenfassung**: Zeigt Statistiken am Ende
6. **ZIP-Archiv**: Erstellt automatisch ein ZIP-Archiv des Export-Ordners

### 4. Angepasste Subskripte

#### export_dashboard.py
- **Neu**: Akzeptiert Command-Line-Argumente:
  - `--url`, `--username`, `--password`
  - `--dashboard-name`, `--dashboard-id`
  - `--export-dir`, `--export-format`
  - `--ca-cert`
- **Kompatibilität**: Funktioniert weiterhin mit Config-Dateien (Legacy-Modus)
- **Klassen-basiert**: Implementiert als `ZabbixDashboardExporter`-Klasse für bessere Modularität

#### map_export.py
- Eigenständiges Skript für Map-Export
- Unterstützt Command-Line-Argumente
- Exportiert Map-Konfiguration mit allen Elementen und Links

#### host_export.py
- Eigenständiges Skript für Host-Export
- Unterstützt Command-Line-Argumente
- Exportiert Host mit Interfaces, Gruppen, Templates, Items und Triggers

## Dateistruktur

```
/workspace/
├── nc_zabbix_config_mgmt.py          # Hauptskript (erweitert)
├── requirements.txt                   # Python-Abhängigkeiten
├── example_export_config.json        # Beispiel-Konfiguration
├── README_EXPORT.md                  # Benutzer-Dokumentation
├── nc_zabbix_config_mgmt/
│   ├── __init__.py
│   ├── config/
│   │   └── __init__.py               # load_config()
│   └── src/
│       ├── dashboard/
│       │   ├── __init__.py
│       │   └── export_dashboard.py   # Dashboard-Export (erweitert)
│       ├── map/
│       │   ├── __init__.py
│       │   └── map_export.py         # Map-Export (neu)
│       └── host/
│           ├── __init__.py
│           └── host_export.py        # Host-Export (neu)
├── nc_zabbix_config_mgmt_source/
│   ├── __init__.py
│   └── config/
│       ├── __init__.py
│       └── config_loader.py          # load_source_config()
└── nc_zabbix_config_mgmt_target/
    ├── __init__.py
    └── config/
        └── __init__.py               # load_target_config()
```

## Verwendungsbeispiele

### Batch-Export

```bash
python nc_zabbix_config_mgmt.py --config example_export_config.json --export ./exports
```

**Ausgabe:**
```
./exports/
├── nc_zabbix_export/
│   ├── dashboard_12345_Network_Overview.json
│   ├── dashboard_12345_Network_Overview.config.json
│   ├── map_67890_Network_Topology.json
│   └── host_11111_Server01.json
└── nc_zabbix_export_20251021_143022.zip
```

### Legacy-Modus (unverändert)

```bash
python nc_zabbix_config_mgmt.py export dashboard
python nc_zabbix_config_mgmt.py import map
```

## Eigenschaften

### ✅ Robustheit
- Fehler werden geloggt, aber der Export wird fortgesetzt
- Zusammenfassung zeigt erfolgreiche und fehlgeschlagene Exporte
- Exit-Code 0 bei Erfolg, 1 bei Fehlern

### ✅ Modularität
- Jedes Subskript kann eigenständig verwendet werden
- Klassen-basierte Implementierung für bessere Wartbarkeit
- Klare Trennung von Verantwortlichkeiten

### ✅ Kompatibilität
- Bestehende Logik wurde nicht entfernt
- Legacy-Modus funktioniert weiterhin
- Beide Modi können parallel verwendet werden

### ✅ Logging
- Ausführliche Debug-Informationen
- Strukturierte Log-Ausgaben
- Fehler werden klar gekennzeichnet

### ✅ Automatisierung
- Automatisches Erstellen des Export-Verzeichnisses
- Automatisches ZIP-Archiv mit Zeitstempel
- Batch-Verarbeitung mehrerer Objekte

## Installation

```bash
pip install -r requirements.txt
```

## Nächste Schritte

Um das System produktionsreif zu machen:

1. **Abhängigkeiten installieren**:
   ```bash
   pip install requests
   ```

2. **Konfiguration anpassen**:
   - Kopieren Sie `example_export_config.json`
   - Passen Sie URL, Credentials und Objektnamen an

3. **Export durchführen**:
   ```bash
   python nc_zabbix_config_mgmt.py --config my_config.json --export ./my_exports
   ```

4. **Optional: Import-Funktionalität erstellen**:
   - Analog zu den Export-Skripten
   - Verwendet die exportierten JSON-Dateien
   - Importiert in Ziel-Zabbix-Instanz

## Hinweise

- Die Subskripte benötigen das `requests`-Modul
- SSL-Zertifikate können über `ca_certificate_path` konfiguriert werden
- Dashboard-Namen müssen exakt mit Zabbix übereinstimmen (case-sensitive)
- Das ZIP-Archiv enthält nur den `nc_zabbix_export`-Ordner
