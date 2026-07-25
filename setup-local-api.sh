#!/usr/bin/env bash
# Збірка локального telegram-bot-api (піднімає ліміт віддачі 50 МБ -> 2000 МБ).
# Перед запуском:  sudo apt install -y cmake libssl-dev zlib1g-dev gperf
# Збірка на 2 ядрах займає ~1.5-3 години. Запускати у фоні:
#   nohup ~/ytbot/setup-local-api.sh > ~/ytbot/build.log 2>&1 &
set -euo pipefail

SRC="$HOME/src/telegram-bot-api"
PREFIX="$HOME/.local"

for dep in cmake g++ make git; do
  command -v "$dep" >/dev/null || { echo "НЕМА $dep -> sudo apt install -y cmake libssl-dev zlib1g-dev gperf"; exit 1; }
done
[ -f /usr/include/openssl/ssl.h ] || { echo "НЕМА libssl-dev -> sudo apt install -y libssl-dev"; exit 1; }

mkdir -p "$(dirname "$SRC")"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" pull --ff-only && git -C "$SRC" submodule update --init --recursive
else
  git clone --recursive https://github.com/tdlib/telegram-bot-api.git "$SRC"
fi

mkdir -p "$SRC/build"
cd "$SRC/build"
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX:PATH="$PREFIX" ..
# -j1: на 2 ядрах / 5 ГБ RAM паралельна збірка tdlib падає по пам'яті
cmake --build . --target install -j1

echo
echo "Готово: $PREFIX/bin/telegram-bot-api"
"$PREFIX/bin/telegram-bot-api" --version
