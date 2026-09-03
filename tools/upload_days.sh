#!/usr/bin/env bash
# Выкладка записей дней на VPS энергетического спортзала — с докачкой.
#
# Записи лежат не в репозитории сайта, а рядом с его данными:
# /var/lib/gym/marathon/dayN.mp4 (превью на :8443 смотрит туда же по
# символическим ссылкам). Файлы по полгигабайта, а связь с VPS рвётся;
# scp начинает заново, поэтому шлём хвост через ssh «cat >>» и при обрыве
# продолжаем с того байта, на котором остановились. Готовый файл
# подменяется одним mv — сайт ни секунды не отдаёт недокачанное.
#
# Запуск из Git Bash:  bash tools/upload_days.sh 1 2 3 4
set -u

HOST=root@91.230.94.147
KEY=~/.ssh/retreat_deploy
DIR=/var/lib/gym/marathon
SRC="C:/Users/Sharp/Desktop/Марафон записи"

remote() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 "$HOST" "$@"; }

for n in "$@"; do
  local_file="$SRC/День $n.mp4"
  size=$(stat -c %s "$local_file")
  part="$DIR/day$n.mp4.part"

  while :; do
    have=$(remote "stat -c %s $part 2>/dev/null || echo 0")
    if [ "$have" -ge "$size" ]; then break; fi
    echo "день $n: $((have / 1048576)) из $((size / 1048576)) МБ, шлю дальше"
    tail -c +$((have + 1)) "$local_file" | remote "cat >> $part" || sleep 3
  done

  remote "mv -f $part $DIR/day$n.mp4 && chown gym:gym $DIR/day$n.mp4 && ls -l $DIR/day$n.mp4"
done
