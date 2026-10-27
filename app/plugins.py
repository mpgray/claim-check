"""Plug-ins: a deployer's own leads, records, tiers and exclusions.

Claim Check checks ANY article with nothing but the open web, and the public
code never ships topic-specific data. A deployer who has data of their own (a
product database, a statistics table, a list of sites they trust or own) adds
it here, without forking.

A plug-in is any object with some of these methods; each is optional:

    leads(claim, report) -> list[str]
        URLs to read for this claim before search results. They are fetched
        and filtered like any other page.
    documents(claim, report) -> list[Document]
        Records from the deployer's own data, judged like pages: the model
        must quote one word for word, and `verify.check` holds the quote to
        the record's text. A `decisive` document is judged first and alone;
        when it settles the claim, that verdict is final.
    tier(url) -> 'official' | 'reference' | 'open' | None
        A tier for a source's address. The best tier any source gives wins.
    excluded(url) -> bool
        True for a site that must never be a source (the publisher's other
        sites, say). Records are not affected: they are the deployer's data,
        not pages found on the web.
    init_app(app)
        Called once with the Flask app: routes, authentication.

Plug-ins come from the `claim_check.plugins` entry-point group and from
`CC_PLUGINS=module:attr,...`. A class is instantiated with no arguments;
anything else is used as it is. A plug-in that fails to load stops the app
from starting: a deployment that silently ran without its data would report
verdicts it was never meant to give. A plug-in that fails during a check is
reported on that check and the check goes on without it.
"""
import importlib
from dataclasses import dataclass
from importlib.metadata import entry_points

GROUP = 'claim_check.plugins'
TIERS = ('official', 'reference', 'open')


@dataclass
class Document:
    url: str                  # where a reader can see the record
    title: str
    text: str                 # what a quote is held to; write it as full lines
    tier: str = 'official'
    date: str = ''            # ISO date the record is as of, if known
    decisive: bool = False    # judged first; its verdict, when it gives one, is final


class PluginError(RuntimeError):
    pass


def _load_spec(spec: str):
    module, _, attr = spec.partition(':')
    if not module or not attr:
        raise PluginError(f'CC_PLUGINS entry {spec!r} is not module:attr.')
    try:
        obj = getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError) as exc:
        raise PluginError(f'Could not load plug-in {spec!r}: {exc}') from exc
    return attr, obj


def _instance(obj):
    return obj() if isinstance(obj, type) else obj


class Plugins:
    """The loaded plug-ins, called together. A failing call is recorded in
    `errors` (the pipeline copies it onto the report) and skipped."""

    def __init__(self, items: list | None = None, names: list[str] | None = None):
        self.items = list(items or [])
        self.names = names or [type(p).__name__ for p in self.items]

    def __bool__(self) -> bool:
        return bool(self.items)

    def _each(self, method: str, *args, errors: list[str] | None = None):
        for name, plugin in zip(self.names, self.items):
            fn = getattr(plugin, method, None)
            if fn is None:
                continue
            try:
                yield fn(*args)
            except Exception as exc:
                if errors is not None:
                    note = f'{name}.{method} failed: {exc}'[:300]
                    if note not in errors:
                        errors.append(note)

    def leads(self, claim, report) -> list[str]:
        out: list[str] = []
        for urls in self._each('leads', claim, report, errors=report.plugin_errors):
            out += [u for u in urls or [] if isinstance(u, str) and u.startswith(('http://', 'https://'))]
        return out

    def documents(self, claim, report) -> list[Document]:
        out: list[Document] = []
        for docs in self._each('documents', claim, report, errors=report.plugin_errors):
            for d in docs or []:
                if isinstance(d, Document) and d.text.strip() and d.url:
                    if d.tier not in TIERS:
                        d.tier = 'open'
                    out.append(d)
        return out

    def tiers(self, url: str) -> list[str]:
        return [t for t in self._each('tier', url) if t in TIERS]

    def excluded(self, url: str) -> bool:
        return any(self._each('excluded', url))

    def init_app(self, app) -> None:
        for name, plugin in zip(self.names, self.items):
            fn = getattr(plugin, 'init_app', None)
            if fn is not None:
                fn(app)


def load(spec: str = '', use_entry_points: bool = True) -> Plugins:
    names, items = [], []
    if use_entry_points:
        for ep in entry_points(group=GROUP):
            try:
                obj = ep.load()
            except Exception as exc:
                raise PluginError(f'Could not load plug-in {ep.name!r}: {exc}') from exc
            names.append(ep.name)
            items.append(_instance(obj))
    for part in (p.strip() for p in spec.split(',')):
        if part:
            name, obj = _load_spec(part)
            names.append(name)
            items.append(_instance(obj))
    return Plugins(items, names)
