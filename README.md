# smhi-weather-mail

En liten tjänst som hämtar dagens punktprognos från **SMHI Open Data SNOW1gv1**
och skickar ett formaterat vädermail.

Tjänsten är byggd för att kunna köras på en vanlig Linux-server utan externa
Python-paket. Schemaläggning görs med en `systemd` timer.

## Funktioner

- SMHI SNOW1gv1 punktprognos.
- Svensk lokal tid via `Europe/Stockholm`.
- HTML-mail + plaintext-alternativ.
- Temperatur min/max.
- Vind, vindbyar och vindriktning.
- Nederbörd och högsta nederbördsrisk.
- Prognospunkter kring 08:00, 12:00, 16:00 och 20:00.
- Flera mottagare via kommaseparerad `MAIL_TO`.
- SMTP med STARTTLS, SSL eller utan TLS.
- Loggning till journald när tjänsten körs via systemd.
- `--dry-run` för test utan att skicka mail.
- Ansible-deploy.
- GitHub Actions för enhetstester.

## Krav

- Linux
- Python 3.11 eller senare
- `tzdata`
- Utgående HTTPS till SMHI
- Tillgång till en SMTP-server

Inga externa Python-paket används.

## SMHI API

Programmet använder SNOW1gv1 punktprognos:

```text
/api/category/snow1g/version/1/geotype/point/lon/{lon}/lat/{lat}/data.json
```

Följande parametrar hämtas:

```text
air_temperature
wind_from_direction
wind_speed
wind_speed_of_gust
relative_humidity
precipitation_amount_mean
probability_of_precipitation
symbol_code
```

SMHI returnerar tider i UTC. Programmet konverterar dem till den tidszon som
anges i `TIMEZONE`, normalt `Europe/Stockholm`.

Källa ska anges som SMHI när data presenteras; mailmallen innehåller därför
`Källa: SMHI Open Data, SNOW1gv1`.

## Lokal provkörning

Kopiera exempelkonfigurationen:

```bash
cp config/smhi-weather-mail.env.example .env
```

Fyll i minst:

```text
SMHI_LAT
SMHI_LON
LOCATION_NAME
```

Läs in variablerna och kör utan att skicka mail:

```bash
set -a
source .env
set +a

python3 src/smhi_weather_mail.py --dry-run \
  --html-output /tmp/smhi-weather-mail.html
```

Det hämtar en riktig prognos från SMHI, skriver plaintext-versionen i terminalen
och HTML-versionen till `/tmp/smhi-weather-mail.html`.

## Mailkonfiguration

Exempel:

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=weather@example.com
SMTP_PASSWORD=...
MAIL_FROM=weather@example.com
MAIL_TO=ulf@example.com,pappa@example.com
```

`SMTP_SECURITY` kan vara:

- `starttls`
- `ssl`
- `none`

När SMTP-delen är konfigurerad skickas mailet med:

```bash
python3 src/smhi_weather_mail.py
```

## Tester

```bash
make test
```

eller:

```bash
python3 -m unittest discover -s tests -v
```

GitHub Actions kör samma tester automatiskt vid push och pull request.


## Publicera på GitHub

Om GitHub CLI (`gh`) redan är installerat och inloggat:

```bash
./scripts/publish-to-github.sh smhi-weather-mail private
```

För ett publikt repo:

```bash
./scripts/publish-to-github.sh smhi-weather-mail public
```

Scriptet initierar Git, skapar första committen, skapar repot på GitHub och pushar
`main`. `.env` och andra riktiga `*.env`-filer ignoreras av Git.

## Installation med Ansible

Exempel finns under `ansible/`.

Inventeringen ska innehålla gruppen:

```yaml
weather_mail_servers:
  hosts:
    monitor:
```

Kopiera variabelexemplet:

```bash
cp ansible/group_vars/weather_mail_servers.yml.example \
   ansible/group_vars/weather_mail_servers.yml
```

**Checka inte in SMTP-lösenordet.** Lägg det i Ansible Vault, exempelvis:

```bash
ansible-vault create ansible/group_vars/vault.yml
```

med:

```yaml
vault_smhi_weather_mail_smtp_password: "hemligt-lösenord"
```

Kör sedan:

```bash
ansible-playbook \
  -i ansible/inventory.example.yml \
  ansible/deploy.yml \
  -e @ansible/group_vars/weather_mail_servers.yml \
  -e @ansible/group_vars/vault.yml \
  --ask-vault-pass
```

## systemd

Timern är förinställd på 06:30 varje dag:

```ini
OnCalendar=*-*-* 06:30:00
Persistent=true
RandomizedDelaySec=60
```

Kontrollera den med:

```bash
systemctl status smhi-weather-mail.timer
systemctl list-timers smhi-weather-mail.timer
```

Provkör tjänsten:

```bash
sudo systemctl start smhi-weather-mail.service
```

Loggar:

```bash
journalctl -u smhi-weather-mail.service
```

## Säkerhet

SMTP-lösenord ska aldrig finnas i Git. Vid manuell installation ligger
konfigurationen i `/etc/smhi-weather-mail.env` med begränsade rättigheter.
Ansible-exemplet använder en separat systemanvändare och en systemd-tjänst med
grundläggande hardening.

## Planerade förbättringar

- SMHI:s varnings-API.
- Mer detaljerad nederbördstext.
- Valbara prognostider.
- Möjlighet att även skicka via exempelvis Pushover/Telegram.
