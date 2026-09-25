# ru-science-search для Claude Code

A Claude Code plugin that searches scholarly articles in CyberLeninka (Russian journals, VAK list) and OpenAlex, in Russian. Install with `/plugin marketplace add boundlessend/yougile-tracking`, then `/plugin install ru-science-search@senya-plugins`.

Плагин Claude Code для поиска научных статей по теме в двух открытых источниках: КиберЛенинке (русскоязычные журналы, отметка перечня ВАК) и OpenAlex (мировая база). На просьбу вроде «найди статьи про терминологическую эквивалентность» агент опрашивает оба источника и выдаёт список: название, авторы, год, журнал, ссылка и строка о том, чем статья подходит к теме. Один файл на Python, только стандартная библиотека.

## Установка

```
/plugin marketplace add boundlessend/yougile-tracking
/plugin install ru-science-search@senya-plugins
```

Нужен Python 3.11 или новее. КиберЛенинка работает на любой системе, OpenAlex только на macOS: скрипт читает ключ из связки ключей через утилиту `security`.

## Ключ OpenAlex

Ключ необязателен. Без него OpenAlex даёт около 100 поисков в сутки, с бесплатным ключом около 1000. Ключ кладут в связку ключей в своём терминале:

```bash
security add-generic-password -U -a "$USER" -s openalex-api-key -w
```

`-w` стоит последним, поэтому ключ спрашивается скрытым вводом и не попадает в историю оболочки. Скрипт отправляет ключ заголовком `Authorization`, в URL и тексты ошибок он не попадает.

## Как пользоваться

Обычно скилл срабатывает сам на просьбы вроде «подбери литературу по теме» или «есть ли статьи ВАК по». Вручную:

```bash
S=$(ls -d ~/.claude/plugins/cache/senya-plugins/ru-science-search/*/skills/ru-science-search/scripts/search.py | tail -1)
python3 "$S" cyberleninka --query "терминологическая эквивалентность" --limit 10 --page 1 --vak-only
python3 "$S" openalex --query "terminological equivalence" --limit 10 --page 1 --year-from 2020
```

`--limit` от 1 до 50, `--page` начинается с 1. `--year-from` работает в обоих источниках, `--vak-only` только в КиберЛенинке. Сетевые сбои и ответы 5xx скрипт повторяет сам.

## Ограничения

- У КиберЛенинки нет документированного API, плагин обращается к внутреннему поиску сайта. При частых запросах сайт включает капчу, и скрипт сообщает, что вместо JSON пришла страница.
- Том, номер и страницы для библиографической ссылки даёт только OpenAlex.
- Счётчик найденного у КиберЛенинки останавливается на 1000, хотя листать можно дальше.
- eLibrary не поддерживается: правила сайта запрещают автоматический поиск.

## Что где лежит

```
ru-science-search/
├── .claude-plugin/plugin.json          манифест плагина
└── skills/ru-science-search/
    ├── SKILL.md                        инструкция для агента
    └── scripts/search.py               клиент обоих источников, он же CLI
```
