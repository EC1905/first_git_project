# Zabbix Config Management - Export Funktionalität

## Übersicht

Dieses Tool ermöglicht den Export von Zabbix Dashboards, Maps und Hosts in einem Batch-Verfahren.

## Installation

Stellen Sie sicher, dass alle erforderlichen Python-Pakete installiert sind:

```bash
pip install requests
```

## Verwendung

### Batch-Export mit Konfigurationsdatei

Um alle in einer Konfigurationsdatei definierten Objekte zu exportieren:

```bash
python nc_zabbix_config_mgmt.py --config <config_file> --export <export_directory>
```

**Beispiel:**

```bash
python nc_zabbix_config_mgmt.py --config example_export_config.json --export ./exports
```

### Konfigurationsdatei Format

Die JSON-Konfigurationsdatei muss folgende Struktur haben:

```json
{
  "url": "https://zabbix.example.com/api_jsonrpc.php",
  "username": "Admin",
  "password": "zabbix",
  "ca_certificate_path": "/path/to/ca-cert.pem",
  "export_format": "json",
  "dashboards": [
    {"name": "Dashboard Name 1"},
    {"name": "Dashboard Name 2"},
    "Dashboard Name 3"
  ],
  "maps": [
    {"name": "Map Name 1"},
    "Map Name 2"
  ],
  "hosts": [
    {"name": "Host Name 1"},
    "Host Name 2"
  ]
}
```

**Felder:**
- `url`: Die Zabbix API URL
- `username`: Zabbix Benutzername
- `password`: Zabbix Passwort
- `ca_certificate_path`: (Optional) Pfad zum CA-Zertifikat für HTTPS-Verbindungen
- `export_format`: Format für den Export (`json` oder `yaml`)
- `dashboards`: Array von Dashboard-Namen (als String oder Objekt mit `name`-Feld)
- `maps`: Array von Map-Namen (als String oder Objekt mit `name`-Feld)
- `hosts`: Array von Host-Namen (als String oder Objekt mit `name`-Feld)

### Einzelner Export (Legacy-Modus)

Das Tool unterstützt auch den Export einzelner Objekte:

```bash
# Dashboard exportieren
python nc_zabbix_config_mgmt.py export dashboard

# Map exportieren
python nc_zabbix_config_mgmt.py export map

# Host exportieren
python nc_zabbix_config_mgmt.py export host
```

## Ausgabe

### Export-Verzeichnis

Alle Exporte werden in einem Unterordner `nc_zabbix_export` im angegebenen Exportverzeichnis gespeichert.

**Beispiel:**
```
./exports/
└── nc_zabbix_export/
    ├── dashboard_12345_Network_Overview.json
    ├── dashboard_12345_Network_Overview.config.json
    ├── map_67890_Network_Topology.json
    └── host_11111_Server01.json
```

### ZIP-Archiv

Nach erfolgreichem Export wird automatisch ein ZIP-Archiv erstellt:

```
./exports/nc_zabbix_export_20251021_143022.zip
```

Der Dateiname enthält einen Zeitstempel im Format `YYYYMMDD_HHMMSS`.

## Fehlerbehandlung

Das Tool ist so konzipiert, dass es bei Fehlern einzelner Exporte nicht abbricht. Stattdessen:

1. Wird der Fehler geloggt
2. Wird mit dem nächsten Export fortgefahren
3. Wird am Ende eine Zusammenfassung ausgegeben

**Beispiel-Ausgabe:**

```
2025-10-21 14:30:22 - INFO - ✓ Dashboard 'Network Overview' exported successfully
2025-10-21 14:30:25 - ERROR - ✗ Dashboard 'Missing Dashboard' export failed: Dashboard not found
2025-10-21 14:30:28 - INFO - ✓ Map 'Network Topology' exported successfully

============================================================
EXPORT SUMMARY
============================================================
Dashboards: 1 succeeded, 1 failed
Maps: 1 succeeded, 0 failed
Hosts: 0 succeeded, 0 failed
============================================================
```

## Erweiterte Funktionen

### Dashboard-Export

Der Dashboard-Export umfasst:
- Dashboard-Konfiguration
- Alle Widgets
- Verknüpfte Maps (inkl. Sub-Maps und Eltern-Maps)
- Hosts, Templates, Trigger, Graphs
- Host-Gruppen
- Items

### Map-Export

Der Map-Export umfasst:
- Map-Konfiguration
- Alle Elemente (Hosts, Groups, Submaps)
- Alle Links

### Host-Export

Der Host-Export umfasst:
- Host-Konfiguration
- Interfaces
- Gruppen
- Parent Templates
- Items
- Triggers

## Troubleshooting

### SSL-Zertifikatsfehler

Wenn Sie Probleme mit SSL-Zertifikaten haben:

1. Geben Sie den Pfad zum CA-Zertifikat in der Konfigurationsdatei an
2. Oder verwenden Sie ein selbst-signiertes Zertifikat

### Authentifizierungsfehler

Stellen Sie sicher, dass:
- Die Zabbix API URL korrekt ist (endet mit `/api_jsonrpc.php`)
- Benutzername und Passwort korrekt sind
- Der Benutzer die erforderlichen Berechtigungen hat

### Objekt nicht gefunden

Wenn ein Dashboard/Map/Host nicht gefunden wird:
- Überprüfen Sie die exakte Schreibweise des Namens
- Stellen Sie sicher, dass das Objekt in Zabbix existiert
- Prüfen Sie die Berechtigungen des Benutzers
