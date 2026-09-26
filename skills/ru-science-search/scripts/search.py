#!/usr/bin/env python3
"""поиск научных статей в киберленинке и openalex, только стандартная библиотека"""

# аннотации остаются строками: системный python3 на macOS это 3.9, где X | None
# в аннотациях без этого импорта не вычисляется
from __future__ import annotations

import argparse
import html
import http.client
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from email.message import Message
from typing import Literal, Optional, TypedDict, Union, cast

SourceName = Literal["cyberleninka", "openalex"]

CYBERLENINKA_BASE_URL = "https://cyberleninka.ru"
CYBERLENINKA_SEARCH_URL = f"{CYBERLENINKA_BASE_URL}/api/search"
CYBERLENINKA_ARTICLE_PREFIX = f"{CYBERLENINKA_BASE_URL}/article/"
# киберленинка фильтрует по годам только при обеих границах: при одной --year-to
# нижней границей уходит год заведомо раньше любого архива
CYBERLENINKA_EARLIEST_YEAR = 1800
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_FIELDS = ",".join(
    (
        "id",
        "doi",
        "display_name",
        "type",
        "publication_year",
        "language",
        "primary_location",
        "biblio",
        "authorships",
        "abstract_inverted_index",
        "cited_by_count",
        "open_access",
    )
)
OPENALEX_KEYCHAIN_SERVICE = "openalex-api-key"
OPENALEX_KEY_ENV = "OPENALEX_API_KEY"
KEYCHAIN_ITEM_NOT_FOUND_EXIT_CODE = 44
USER_AGENT = "ru-science-search/1.0 (personal academic search)"
TIMEOUT_SECONDS = 20
RETRY_DELAYS_SECONDS: tuple[float, ...] = (1.0, 3.0)
RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})
TOO_MANY_REQUESTS_STATUS = 429
BODY_SNIPPET_CHARS = 500
CYBERLENINKA_FOUND_CAP = 1000
CYBERLENINKA_VAK_CATALOG_ID = 8
MAX_LIMIT = 50
MAX_SHOWN_AUTHORS = 5
# агенту нужна одна строка о том, чем статья подходит, а целиком аннотации занимают
# больше половины вывода; полный текст есть по ссылке
ABSTRACT_MAX_CHARS = 320
TAG_PATTERN = re.compile(r"<[^>]+>")
SOURCE_TITLES: Mapping[SourceName, str] = {"cyberleninka": "КиберЛенинка", "openalex": "OpenAlex"}


class RetryableRequestError(RuntimeError):
    """сбой, который имеет смысл повторить: сеть, 5xx"""


class RateLimitError(RuntimeError):
    """429: у openalex кончился суточный бюджет, у киберленинки слишком частые запросы; повтор не поможет"""


class SearchRequestError(RuntimeError):
    """запрос к источнику не удался окончательно"""


class UnexpectedResponseError(RuntimeError):
    """источник ответил не в ожидаемом формате, например страницей с капчей"""


class KeychainError(RuntimeError):
    """связка ключей macOS не отдала запись по причине, отличной от её отсутствия"""


class CyberleninkaCatalog(TypedDict):
    acronym: str


class CyberleninkaArticle(TypedDict):
    name: str
    annotation: str | None
    link: str
    authors: list[str] | None
    year: int | None
    journal: str
    catalogs: list[CyberleninkaCatalog] | None


class CyberleninkaResponse(TypedDict):
    found: int
    articles: list[CyberleninkaArticle]


class OpenalexSource(TypedDict):
    display_name: str


class OpenalexLocation(TypedDict):
    landing_page_url: str | None
    source: OpenalexSource | None


class OpenalexAuthor(TypedDict):
    display_name: str


class OpenalexAuthorship(TypedDict):
    author: OpenalexAuthor


class OpenalexOpenAccess(TypedDict):
    oa_url: str | None


class OpenalexBiblio(TypedDict):
    volume: str | None
    issue: str | None
    first_page: str | None
    last_page: str | None


class OpenalexWork(TypedDict):
    id: str
    doi: str | None
    display_name: str | None
    type: str
    publication_year: int | None
    language: str | None
    primary_location: OpenalexLocation | None
    biblio: OpenalexBiblio
    authorships: list[OpenalexAuthorship]
    abstract_inverted_index: dict[str, list[int]] | None
    cited_by_count: int
    open_access: OpenalexOpenAccess


class OpenalexMeta(TypedDict):
    count: int


class OpenalexResponse(TypedDict):
    meta: OpenalexMeta
    results: list[OpenalexWork]


@dataclass(frozen=True)
class SearchArgs:
    source: SourceName
    query: str
    limit: int
    page: int
    year_from: int | None
    year_to: int | None
    vak_only: bool


@dataclass(frozen=True)
class DetailsArgs:
    url: str


@dataclass(frozen=True)
class ArticleDetails:
    """готовые библиографические ссылки со страницы статьи киберленинки"""

    url: str
    gost_print: str
    gost_electronic: str
    doi: str


@dataclass(frozen=True)
class HttpRequest:
    url: str
    method: str
    body: bytes | None
    headers: Mapping[str, str]


@dataclass(frozen=True)
class Paper:
    title: str
    authors: tuple[str, ...]
    year: int | None
    venue: str
    url: str
    labels: tuple[str, ...]
    abstract: str


@dataclass(frozen=True)
class SearchResult:
    source: SourceName
    total: int
    papers: tuple[Paper, ...]


def log_event(event: str, fields: Mapping[str, str | int | float]) -> None:
    record: dict[str, str | int | float] = {"level": "warning", "event": event}
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), file=sys.stderr)


def describe_request(request: HttpRequest) -> str:
    if request.body is None:
        return f"{request.method} {request.url}"
    return f"{request.method} {request.url} body={request.body.decode('utf-8')}"


def build_cyberleninka_request(args: SearchArgs, current_year: int) -> HttpRequest:
    """киберленинка молча игнорирует одну границу года без другой, поэтому недостающая
    подставляется: снизу CYBERLENINKA_EARLIEST_YEAR, сверху текущий год"""
    payload: dict[str, str | int | list[int]] = {
        "mode": "articles",
        "q": args.query,
        "size": args.limit,
        "from": (args.page - 1) * args.limit,
    }
    if args.year_from is not None or args.year_to is not None:
        if args.year_from is not None:
            payload["year_from"] = args.year_from
        else:
            payload["year_from"] = CYBERLENINKA_EARLIEST_YEAR
        if args.year_to is not None:
            payload["year_to"] = args.year_to
        else:
            payload["year_to"] = current_year
    if args.vak_only:
        payload["catalogs"] = [CYBERLENINKA_VAK_CATALOG_ID]
    return HttpRequest(
        url=CYBERLENINKA_SEARCH_URL,
        method="POST",
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )


def read_openalex_api_key() -> str | None:
    """ключ openalex: сначала переменная окружения, затем связка ключей macOS; None, если нет нигде

    без ключа openalex работает, только с меньшим суточным бюджетом, поэтому на системах
    без связки ключей его отсутствие не ошибка
    """
    from_env = os.environ.get(OPENALEX_KEY_ENV, "").strip()
    if from_env:
        return from_env
    if platform.system() != "Darwin":
        return None
    completed = subprocess.run(
        ["security", "find-generic-password", "-s", OPENALEX_KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == KEYCHAIN_ITEM_NOT_FOUND_EXIT_CODE:
        return None
    if completed.returncode != 0:
        raise KeychainError(
            f"security find-generic-password -s {OPENALEX_KEYCHAIN_SERVICE} "
            f"завершился с кодом {completed.returncode}: {completed.stderr.strip()}"
        )
    api_key = completed.stdout.strip()
    if not api_key:
        raise KeychainError(f"запись {OPENALEX_KEYCHAIN_SERVICE} в связке ключей пустая")
    return api_key


def build_openalex_request(args: SearchArgs, api_key: str | None) -> HttpRequest:
    """ключ уходит заголовком, а не в url: url попадает в тексты ошибок"""
    params = {"search": args.query, "per_page": str(args.limit), "page": str(args.page), "select": OPENALEX_FIELDS}
    year_filters: list[str] = []
    if args.year_from is not None:
        year_filters.append(f"publication_year:>{args.year_from - 1}")
    if args.year_to is not None:
        year_filters.append(f"publication_year:<{args.year_to + 1}")
    if year_filters:
        # запятая в filter openalex означает «и»
        params["filter"] = ",".join(year_filters)
    headers = {"User-Agent": USER_AGENT}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    return HttpRequest(
        url=f"{OPENALEX_WORKS_URL}?{urllib.parse.urlencode(params)}",
        method="GET",
        body=None,
        headers=headers,
    )


def describe_rate_limit_headers(headers: Message) -> str:
    """retry-after и x-ratelimit-* из ответа 429: по ним видно, когда откроется квота"""
    relevant: list[str] = []
    for name, value in headers.items():
        lowered = name.lower()
        if lowered == "retry-after" or lowered.startswith("x-ratelimit-"):
            relevant.append(f"{name}={value}")
    if not relevant:
        return "заголовков лимита в ответе нет"
    return ", ".join(relevant)


def send_once(request: HttpRequest) -> str:
    raw_request = urllib.request.Request(
        request.url, data=request.body, headers=dict(request.headers), method=request.method
    )
    try:
        with urllib.request.urlopen(raw_request, timeout=TIMEOUT_SECONDS) as response:
            return cast(bytes, response.read()).decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:BODY_SNIPPET_CHARS]
        message = f"HTTP {error.code} на {describe_request(request)}; ответ: {body}"
        if error.code == TOO_MANY_REQUESTS_STATUS:
            raise RateLimitError(f"{message}; {describe_rate_limit_headers(error.headers)}") from error
        if error.code in RETRYABLE_STATUSES:
            raise RetryableRequestError(message) from error
        raise SearchRequestError(message) from error
    # обрыв при чтении ответа urllib не оборачивает в URLError: он приходит как OSError или HTTPException
    except (OSError, http.client.HTTPException) as error:
        raise RetryableRequestError(f"сетевая ошибка на {describe_request(request)}: {error!r}") from error


def fetch_text(request: HttpRequest) -> str:
    """отправляет запрос, повторяя сетевые сбои и 5xx с паузами из RETRY_DELAYS_SECONDS"""
    last_error: RetryableRequestError | None = None
    for delay in (0.0, *RETRY_DELAYS_SECONDS):
        if last_error is not None:
            log_event("retry", {"request": describe_request(request), "error": str(last_error), "delay_s": delay})
            time.sleep(delay)
        try:
            return send_once(request)
        except RetryableRequestError as error:
            last_error = error
    raise SearchRequestError(f"все попытки исчерпаны: {last_error}") from last_error


def load_json(text: str, source: SourceName) -> object:
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError as error:
        raise UnexpectedResponseError(
            f"{SOURCE_TITLES[source]} вернул не JSON, вероятно капчу; ответ: {text[:BODY_SNIPPET_CHARS]}"
        ) from error


def strip_markup(text: str) -> str:
    return html.unescape(TAG_PATTERN.sub("", text)).strip()


def restore_abstract(inverted_index: Mapping[str, Sequence[int]] | None) -> str:
    """собирает текст аннотации из инвертированного индекса openalex: слово -> позиции"""
    if inverted_index is None:
        return ""
    positioned: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for position in positions:
            positioned.append((position, word))
    positioned.sort()
    return " ".join(word for _, word in positioned)


def cyberleninka_paper(article: CyberleninkaArticle) -> Paper:
    labels = tuple(catalog["acronym"] for catalog in article["catalogs"] or [])
    return Paper(
        title=strip_markup(article["name"]),
        authors=tuple(article["authors"] or []),
        year=article["year"],
        venue=article["journal"],
        url=CYBERLENINKA_BASE_URL + article["link"],
        labels=labels,
        abstract=strip_markup(article["annotation"] or ""),
    )


def openalex_venue(location: OpenalexLocation | None) -> str:
    if location is None or location["source"] is None:
        return ""
    return location["source"]["display_name"]


def openalex_url(work: OpenalexWork) -> str:
    """ссылка на работу: doi, если есть, иначе карточка openalex"""
    if work["doi"] is not None:
        return work["doi"]
    return work["id"]


def openalex_biblio(biblio: OpenalexBiblio) -> str:
    """том, выпуск и страницы для библиографической ссылки; пустая строка, если openalex их не знает"""
    parts: list[str] = []
    if biblio["volume"] is not None:
        parts.append(f"т. {biblio['volume']}")
    if biblio["issue"] is not None:
        parts.append(f"№ {biblio['issue']}")
    first_page = biblio["first_page"]
    last_page = biblio["last_page"]
    if first_page is not None and last_page is not None and first_page != last_page:
        parts.append(f"с. {first_page}-{last_page}")
    elif first_page is not None:
        parts.append(f"с. {first_page}")
    return ", ".join(parts)


def openalex_labels(work: OpenalexWork) -> tuple[str, ...]:
    labels: list[str] = []
    biblio = openalex_biblio(work["biblio"])
    if biblio:
        labels.append(biblio)
    labels.append(f"тип: {work['type']}")
    labels.append(f"цитирований: {work['cited_by_count']}")
    if work["language"] is not None:
        labels.append(f"язык: {work['language']}")
    oa_url = work["open_access"]["oa_url"]
    if oa_url is not None:
        labels.append(f"открытый текст: {oa_url}")
    return tuple(labels)


def openalex_paper(work: OpenalexWork) -> Paper:
    return Paper(
        title=work["display_name"] or "",
        authors=tuple(authorship["author"]["display_name"] for authorship in work["authorships"]),
        year=work["publication_year"],
        venue=openalex_venue(work["primary_location"]),
        url=openalex_url(work),
        labels=openalex_labels(work),
        abstract=restore_abstract(work["abstract_inverted_index"]),
    )


def parse_cyberleninka(text: str) -> SearchResult:
    data = cast(CyberleninkaResponse, load_json(text, "cyberleninka"))
    try:
        papers = tuple(cyberleninka_paper(article) for article in data["articles"])
        return SearchResult(source="cyberleninka", total=data["found"], papers=papers)
    except (KeyError, TypeError) as error:
        raise UnexpectedResponseError(
            f"КиберЛенинка изменила формат ответа: {error!r}; ответ: {text[:BODY_SNIPPET_CHARS]}"
        ) from error


def parse_openalex(text: str) -> SearchResult:
    data = cast(OpenalexResponse, load_json(text, "openalex"))
    try:
        papers = tuple(openalex_paper(work) for work in data["results"])
        return SearchResult(source="openalex", total=data["meta"]["count"], papers=papers)
    except (KeyError, TypeError) as error:
        raise UnexpectedResponseError(
            f"OpenAlex изменил формат ответа: {error!r}; ответ: {text[:BODY_SNIPPET_CHARS]}"
        ) from error


def run_search(args: SearchArgs) -> SearchResult:
    if args.source == "cyberleninka":
        request = build_cyberleninka_request(args, date.today().year)
        return parse_cyberleninka(fetch_text(request))
    api_key = read_openalex_api_key()
    if api_key is None:
        log_event(
            "openalex_without_api_key",
            {"env_var": OPENALEX_KEY_ENV, "keychain_service": OPENALEX_KEYCHAIN_SERVICE},
        )
    request = build_openalex_request(args, api_key)
    return parse_openalex(fetch_text(request))


def read_page_quote(page: str, key: str) -> str | None:
    """строковое поле из объекта quotes, который страница статьи передаёт своему скрипту

    значение записано литералом js с экранированием json (\\/, \\u0022), поэтому
    раскодируется через json
    """
    found = re.search(r"\b" + key + r':\s*"((?:[^"\\]|\\.)*)"', page)
    if found is None:
        return None
    return " ".join(cast(str, json.loads(f'"{found.group(1)}"')).split())


def parse_cyberleninka_article(page: str, url: str) -> ArticleDetails:
    gost_print = read_page_quote(page, "gost_print")
    gost_electronic = read_page_quote(page, "gost_electronic")
    if gost_print is None or gost_electronic is None:
        raise UnexpectedResponseError(
            f"на странице {url} нет блока цитирования, вероятно капча или сменилась вёрстка; "
            f"начало ответа: {page[:BODY_SNIPPET_CHARS]}"
        )
    return ArticleDetails(
        url=url,
        gost_print=gost_print,
        gost_electronic=gost_electronic,
        doi=read_page_quote(page, "doi") or "",
    )


def run_details(args: DetailsArgs) -> ArticleDetails:
    request = HttpRequest(url=args.url, method="GET", body=None, headers={"User-Agent": USER_AGENT})
    return parse_cyberleninka_article(fetch_text(request), args.url)


def format_details(details: ArticleDetails) -> str:
    lines = [
        f"КиберЛенинка: {details.url}",
        f"ГОСТ, печатное издание: {details.gost_print}",
        f"ГОСТ, электронный ресурс: {details.gost_electronic}",
    ]
    if details.doi:
        lines.append(f"DOI: https://doi.org/{details.doi}")
    return "\n".join(lines)


def shorten(text: str, limit: int) -> str:
    """обрезать по границе слова, отметив обрезку многоточием"""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def format_authors(authors: Sequence[str]) -> str:
    shown = ", ".join(authors[:MAX_SHOWN_AUTHORS])
    hidden_count = len(authors) - MAX_SHOWN_AUTHORS
    if hidden_count > 0:
        return f"{shown} и ещё {hidden_count}"
    return shown


def format_paper(number: int, paper: Paper) -> str:
    details = [str(paper.year) if paper.year is not None else "год не указан"]
    if paper.venue:
        details.append(paper.venue)
    details.extend(paper.labels)
    lines = [f"{number}. {paper.title}"]
    if paper.authors:
        lines.append(f"   Авторы: {format_authors(paper.authors)}")
    lines.append(f"   {' | '.join(details)}")
    lines.append(f"   Ссылка: {paper.url}")
    if paper.abstract:
        lines.append(f"   Аннотация: {shorten(paper.abstract, ABSTRACT_MAX_CHARS)}")
    return "\n".join(lines)


def format_total(result: SearchResult) -> str:
    """киберленинка не считает дальше 1000, хотя листать можно и глубже"""
    if result.source == "cyberleninka" and result.total >= CYBERLENINKA_FOUND_CAP:
        return f"{result.total}+"
    return str(result.total)


def format_result(result: SearchResult, args: SearchArgs) -> str:
    header = (
        f"{SOURCE_TITLES[result.source]}: найдено {format_total(result)}, "
        f"страница {args.page}, показано {len(result.papers)}"
    )
    first_number = (args.page - 1) * args.limit + 1
    blocks = [format_paper(first_number + index, paper) for index, paper in enumerate(result.papers)]
    return "\n\n".join([header, *blocks])


def non_empty_query(value: str) -> str:
    query = value.strip()
    if not query:
        raise argparse.ArgumentTypeError("запрос не может быть пустым")
    return query


def limit_value(value: str) -> int:
    limit = int(value)
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"--limit должен быть от 1 до {MAX_LIMIT}, получено {limit}")
    return limit


def page_value(value: str) -> int:
    page = int(value)
    if page < 1:
        raise argparse.ArgumentTypeError(f"--page начинается с 1, получено {page}")
    return page


def article_url(value: str) -> str:
    url = value.strip()
    if not url.startswith(CYBERLENINKA_ARTICLE_PREFIX):
        raise argparse.ArgumentTypeError(f"нужна ссылка на статью вида {CYBERLENINKA_ARTICLE_PREFIX}..., получено {url}")
    return url


def add_search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--query", required=True, type=non_empty_query)
    parser.add_argument("--limit", required=True, type=limit_value)
    parser.add_argument("--page", required=True, type=page_value)
    parser.add_argument("--year-from", type=int)
    parser.add_argument("--year-to", type=int)


def parse_args(argv: Sequence[str]) -> Union[SearchArgs, DetailsArgs]:
    parser = argparse.ArgumentParser(description="поиск научных статей в киберленинке и openalex")
    commands = parser.add_subparsers(dest="command", required=True)
    cyberleninka = commands.add_parser("cyberleninka", help="поиск в киберленинке")
    add_search_arguments(cyberleninka)
    cyberleninka.add_argument("--vak-only", action="store_true", help="только журналы из перечня ВАК")
    openalex = commands.add_parser("openalex", help="поиск в openalex")
    add_search_arguments(openalex)
    # в openalex отметки ВАК нет
    openalex.set_defaults(vak_only=False)
    details = commands.add_parser("details", help="готовые ссылки по ГОСТ и DOI статьи киберленинки")
    details.add_argument("--url", required=True, type=article_url)
    namespace = parser.parse_args(argv)
    if namespace.command == "details":
        return DetailsArgs(url=cast(str, namespace.url))
    year_from = cast(Optional[int], namespace.year_from)
    year_to = cast(Optional[int], namespace.year_to)
    if year_from is not None and year_to is not None and year_from > year_to:
        parser.error(f"--year-from {year_from} позже --year-to {year_to}")
    return SearchArgs(
        source=cast(SourceName, namespace.command),
        query=cast(str, namespace.query),
        limit=cast(int, namespace.limit),
        page=cast(int, namespace.page),
        year_from=year_from,
        year_to=year_to,
        vak_only=cast(bool, namespace.vak_only),
    )


def main() -> None:
    args = parse_args(sys.argv[1:])
    try:
        if isinstance(args, DetailsArgs):
            output = format_details(run_details(args))
        else:
            output = format_result(run_search(args), args)
    except (RateLimitError, SearchRequestError, UnexpectedResponseError, KeychainError) as error:
        sys.exit(f"{type(error).__name__}: {error}")
    print(output)


if __name__ == "__main__":
    main()
