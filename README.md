# ru-science-search для Claude Code и Codex

Плагин для Claude Code и Codex: поиск научных статей в КиберЛенинке (русскоязычные журналы, отметка перечня ВАК) и OpenAlex (мировая база). На просьбу «найди статьи про ...» агент опрашивает оба источника и выдаёт список: название, авторы, год, журнал, ссылка и строка о том, чем статья подходит к теме.

## Требования

Python 3.9 или новее, только стандартная библиотека. Оба источника работают на любой системе.

## Установка

### Claude Code

```
/plugin marketplace add boundlessend/yougile-tracking
/plugin install ru-science-search@senya-plugins
```

Маркетплейс `senya-plugins` лежит в репозитории `yougile-tracking`, поэтому адрес такой.

### Codex

```bash
codex plugin marketplace add boundlessend/yougile-tracking
codex plugin add ru-science-search@senya-plugins
```

В приложении Codex откройте каталог плагинов, выберите источник **Senya Plugins** и установите **ru-science-search**. После установки откройте новый чат.

Для установки только скилла попросите Codex:

```text
$skill-installer install https://github.com/boundlessend/ru-science-search/tree/main/skills/ru-science-search
```

### Другие агенты

Скилл сделан по формату [Agent Skills](https://agentskills.io). Выберите установщик, который поддерживает ваш агент:

```
npx skills add boundlessend/ru-science-search
gh skill install boundlessend/ru-science-search ru-science-search
gemini extensions install https://github.com/boundlessend/ru-science-search
```

Тогда `search.py` лежит в папке `scripts/` там, куда агент поставил скилл.

## Ключ OpenAlex

Ключ необязателен: без него OpenAlex даёт около 100 поисков в сутки, с бесплатным ключом около 1000. Сначала ключ ищется в переменной `OPENALEX_API_KEY`, затем, на macOS, в связке ключей. Положить его туда можно в своём терминале:

```bash
security add-generic-password -U -a "$USER" -s openalex-api-key -w
```

Ключ спрашивается скрытым вводом и не попадает в историю оболочки.

## Как пользоваться

Скилл срабатывает сам на просьбы вроде «подбери литературу по теме» или «есть ли статьи ВАК по». Вручную:

Путь для плагина Claude Code:

```bash
S=$(ls -d ~/.claude/plugins/cache/senya-plugins/ru-science-search/*/skills/ru-science-search/scripts/search.py | tail -1)
```

Для Codex возьмите путь к установленному скиллу из его расположения в списке skills и задайте `S="<skill dir>/scripts/search.py"`. Затем:

```bash
python3 "$S" cyberleninka --query "терминологическая эквивалентность" --limit 10 --page 1 --vak-only
python3 "$S" openalex --query "terminological equivalence" --limit 10 --page 1 --year-from 2020
```

`--limit` от 1 до 50, `--page` начинается с 1. `--year-from` и `--year-to` работают в обоих источниках, `--vak-only` только в КиберЛенинке.

Готовые ссылки по ГОСТ с номером выпуска и страницами для статьи КиберЛенинки:

```bash
python3 "$S" details --url "https://cyberleninka.ru/article/n/..."
```

## Ограничения

- У КиберЛенинки нет документированного API: плагин обращается к внутреннему поиску сайта, и при частых запросах сайт включает капчу. `details` делает по запросу на статью.
- Аннотации в выдаче обрезаны до 320 символов.
- eLibrary не поддерживается: правила сайта запрещают автоматический поиск.

## Обновление

Claude Code:

```bash
claude plugin marketplace update senya-plugins
claude plugin update ru-science-search@senya-plugins
```

Codex:

```bash
codex plugin marketplace upgrade senya-plugins
codex plugin add ru-science-search@senya-plugins
```

После обновления перезапустите агент.

## Лицензия

BSD 3-Clause, текст в `LICENSE`.
