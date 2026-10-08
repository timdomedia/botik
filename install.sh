#!/usr/bin/env bash
# Установка/обновление бота на VPS одной командой (Ubuntu/Debian, от root):
#   curl -fsSL https://raw.githubusercontent.com/timdomedia/botik/claude/tilda-telegram-orders-bot-iplflz/install.sh | bash
# Повторный запуск = обновление: .env, база и каталог сохраняются.
set -euo pipefail

REPO="https://github.com/timdomedia/botik.git"
BRANCH="${BOTIK_BRANCH:-claude/tilda-telegram-orders-bot-iplflz}"
DIR="${BOTIK_DIR:-/opt/botik}"

say()  { printf '\n\033[1;32m▶ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✖ %s\033[0m\n' "$*"; exit 1; }
ask()  {  # вопрос с ответом по умолчанию; читает с терминала, даже когда скрипт пришёл через curl | bash
  local q="$1" def="${2:-}" a
  if { true </dev/tty; } 2>/dev/null; then read -r -p "$q${def:+ [$def]}: " a </dev/tty
  else read -r -p "$q${def:+ [$def]}: " a; fi
  echo "${a:-$def}"
}
envget() { grep -E "^$1=" "$DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true; }
envset() {  # envset KEY VALUE — заменить или дописать строку в .env
  if grep -qE "^$1=" "$DIR/.env"; then
    python3 - "$DIR/.env" "$1" "$2" <<'PY'
import sys, re
path, key, value = sys.argv[1:]
text = open(path, encoding="utf-8").read()
text = re.sub(rf"(?m)^{re.escape(key)}=.*$", lambda m: f"{key}={value}", text)
open(path, "w", encoding="utf-8").write(text)
PY
  else
    echo "$1=$2" >> "$DIR/.env"
  fi
}

[ "$(id -u)" = 0 ] || die "Запусти от root (sudo -i)."

say "Пакеты"
command -v git >/dev/null && command -v curl >/dev/null && command -v python3 >/dev/null \
  && command -v ss >/dev/null || { apt-get update -qq && apt-get install -y -qq git curl python3 iproute2 >/dev/null; }
if ! command -v docker >/dev/null; then
  say "Ставлю Docker"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "Нет плагина docker compose: apt-get install docker-compose-plugin"

say "Код бота → $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q "$BRANCH"
  git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"

# --- .env ---
if [ ! -f .env ]; then
  say "Настройка (один раз)"
  cp .env.example .env
  while :; do
    token="$(ask 'Новый токен @splitfinance_bot из BotFather')"
    me="$(curl -fsS "https://api.telegram.org/bot${token}/getMe" 2>/dev/null || true)"
    if echo "$me" | grep -q '"ok":true'; then
      echo "  ок: @$(echo "$me" | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["username"])')"
      break
    fi
    warn "Telegram не принял токен, попробуй ещё раз."
  done
  envset BOT_TOKEN "$token"
  envset TILDA_TOKEN "$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  port=8080
  while ss -ltn 2>/dev/null | grep -q ":$port "; do port=$((port + 1)); done
  envset PORT "$port"
  case "$(ask 'Вычитать налог УСН 6% из прибыли? (y/n)' n)" in
    y|Y|д|Д) envset TAX_PERCENT 6 ;;
  esac
else
  say ".env уже есть — оставляю как есть"
fi
[ -f products.yaml ] || printf '# Себестоимость удобнее заводить из чата: /cost Шуба 9000\nproducts: []\n' > products.yaml
[ -f service_account.json ] || echo '{}' > service_account.json
mkdir -p data

PORT="$(envget PORT)"; PORT="${PORT:-8080}"
TILDA_TOKEN="$(envget TILDA_TOKEN)"
PUBLIC_IP="$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
port_busy() { ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; }
nginx_owns_443() { ss -ltnp 2>/dev/null | grep -E "[:.]443 " | grep -q nginx; }

# --- как Тильда достучится до бота по https ---
#   nginx — на сервере есть nginx с доменом: /tilda добавляется в него
#   caddy — порты 80/443 свободны: Caddy сам получит сертификат (свой домен не нужен — IP.sslip.io)
#   relay — 443 занят (часто так на прокси-серверах: xray/VLESS): https-адрес даёт nginx
#           другого сервера (например, где tg-finance), а он пересылает /tilda сюда
MODE="$(envget PUBLIC_MODE)"
if [ -z "$MODE" ]; then
  say "Как принимать заказы Тильды"
  if command -v nginx >/dev/null && { ! port_busy 443 || nginx_owns_443; }; then
    MODE=nginx
  elif ! port_busy 80 && ! port_busy 443; then
    MODE=caddy
  else
    MODE=relay
    echo "  порт 443 занят: $(ss -ltnp 2>/dev/null | grep -E "[:.]443 " | grep -oE 'users:\(\("[^"]+' | head -1 | cut -d'"' -f2)"
  fi
  echo "  вариант: $MODE"
  envset PUBLIC_MODE "$MODE"
  case "$MODE" in
    nginx)
      d="$(ask 'Домен этого сервера, который уже открывается по https (например fin.example.ru)')" ;;
    caddy)
      d="$(ask "Свой домен, направленный на $PUBLIC_IP (Enter — бесплатный $PUBLIC_IP.sslip.io)" "$PUBLIC_IP.sslip.io")"
      envset COMPOSE_PROFILES caddy ;;
    relay)
      d="$(ask 'Домен другого сервера с nginx и https (например, где tg-finance)')"
      envset BIND 0.0.0.0 ;;
  esac
  d="${d#https://}"; d="${d%%/*}"
  envset PUBLIC_DOMAIN "$d"
fi
DOMAIN="$(envget PUBLIC_DOMAIN)"

say "Запуск"
docker compose up -d --build --remove-orphans
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 || { docker compose logs --tail 50; die "Бот не поднялся — лог выше."; }
echo "  бот работает на порту $PORT"

RELAY_NOTE=""
case "$MODE" in
nginx)
  say "nginx: подключаю /tilda на $DOMAIN"
  mkdir -p /etc/nginx/snippets
  cat > /etc/nginx/snippets/botik.conf <<EOF
# Заказы Тильды → бот (ставит install.sh из /opt/botik)
location ^~ /tilda {
    proxy_pass http://127.0.0.1:$PORT;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
}
EOF
  conf="$(grep -rlsE "server_name[^;]*\b${DOMAIN//./\\.}\b" /etc/nginx/sites-enabled /etc/nginx/conf.d | head -1 || true)"
  if [ -z "$conf" ]; then
    warn "Не нашёл server-блок для $DOMAIN. Добавь вручную в его server { listen 443 ... }:"
    echo "    include snippets/botik.conf;"
  elif grep -q "snippets/botik.conf" "$conf"; then
    echo "  уже подключено в $conf"
  else
    cp "$conf" "$conf.bak-botik"
    rc=0
    python3 - "$conf" "$DOMAIN" <<'PY' || rc=$?
import re, sys
path, domain = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()

def blocks(s):
    """Все server { ... } на верхнем уровне: (start, end)."""
    out, i = [], 0
    for m in re.finditer(r"(?m)^\s*server\s*\{", s):
        if m.start() < i:
            continue
        depth, j = 0, m.end() - 1
        while j < len(s):
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                break
            j += 1
        out.append((m.start(), j))
        i = j
    return out

name_re = re.compile(r"server_name[^;]*\b" + re.escape(domain) + r"\b[^;]*;")
for start, end in blocks(text):
    body = text[start:end]
    if name_re.search(body) and re.search(r"listen[^;]*(443|ssl)", body):
        m = name_re.search(body)
        pos = start + m.end()
        text = text[:pos] + "\n    include snippets/botik.conf;" + text[pos:]
        open(path, "w", encoding="utf-8").write(text)
        print("  добавил include в", path)
        sys.exit(0)
print("  в", path, "нет https-блока для", domain)
sys.exit(3)
PY
    if [ $rc -eq 0 ] && nginx -t 2>/dev/null; then
      systemctl reload nginx
    else
      mv "$conf.bak-botik" "$conf"
      warn "Автоматически не вышло, конфиг nginx не тронут. Добавь вручную в server { listen 443 ... } для $DOMAIN:"
      echo "    include snippets/botik.conf;"
      echo "  и выполни: nginx -t && systemctl reload nginx"
    fi
  fi
;;
caddy)
  say "Caddy получает сертификат для $DOMAIN"
  for _ in $(seq 1 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/" || true)" != 000 ] && break
    sleep 2
  done
  ;;
relay)
  if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null && echo "  открыл порт $PORT в ufw"
  fi
  RELAY_NOTE="
0. На сервере с доменом $DOMAIN добавь в nginx, в server { listen 443 ... } этого домена:

     location ^~ /tilda {
         proxy_pass http://$PUBLIC_IP:$PORT;
         proxy_set_header Host \$host;
     }

   и выполни: nginx -t && systemctl reload nginx
   (без секретного токена в ссылке бот заказы не примет, так что открытый порт безопасен)
"
  ;;
esac

URL="https://$DOMAIN/tilda?token=$TILDA_TOKEN"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST -d test=test "$URL" || true)"
if [ "$code" = 200 ]; then
  say "Снаружи всё доступно ✅"
elif [ "$MODE" = relay ]; then
  warn "Адрес заработает после шага 0 ниже (nginx на $DOMAIN)."
else
  warn "Проверка $URL вернула код $code — адрес снаружи пока не отвечает."
fi

cat <<EOF

────────────────────────────────────────────────────────
Готово. Осталось:
$RELAY_NOTE
1. Тильда → Настройки сайта → Формы → Webhook → добавить:
     $URL
   и подключить его к форме корзины (вебхук tg-finance не трогать).

2. Telegram: добавь бота в чат с Даней, сделай администратором,
   напиши в чате  /setup

3. Себестоимость товаров — прямо в чате:  /cost Шуба 9000
   Верхняя одежда по размерам:            /outer Шуба

Логи:        cd $DIR && docker compose logs -f
Обновление:  запусти эту же команду ещё раз
────────────────────────────────────────────────────────
EOF
