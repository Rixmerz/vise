"""El índice tiene que dar la misma respuesta que el escaneo completo.

El índice existe solo por velocidad: `experience_injector` cae al escaneo
completo del store cuando está frío y usa los buckets cuando está caliente. Si
las dos rutas no coinciden, la memoria de experiencia devuelve cosas distintas
según cuándo se llame — que es peor que no tener índice, porque es intermitente.

Encontrado corriendo el hook como subproceso real dos veces sobre el mismo
store, una fría y otra caliente, y comparando stderr. El bucketing anterior
agrupaba por el directorio padre del patrón y en la consulta cargaba solo el
bucket del padre inmediato del target más la raíz. Pero un `*` de glob abarca
`/`, así que `src/vise/*` hace fullmatch contra `src/vise/cli/run_cmd.py` con
path_score 1.0 — el máximo — y vivía en un bucket que la consulta nunca abría.
No se perdía un candidato marginal: se perdía el mejor.

    COLD:  [0.72] patron ancho src/vise/*   <- el mejor
           [0.66] mismo parent
           [0.64] patron raiz
    WARM:  [0.66] mismo parent              <- el mejor desaparecido
           [0.64] patron raiz

Segundo desajuste, más silencioso: el builder escribía un bucket `_nopattern`
para las entradas sin `file_pattern` y la consulta no lo abría nunca. Esas
entradas puntúan por keywords, dominio y confianza, así que el escaneo completo
sí las ofrece.

El test de contrato que ya existía documentaba una "known intentional
difference" para entradas *sin relevancia de path* en directorios ajenos. Nada
de esto cae ahí: una entrada que hace fullmatch tiene la relevancia de path
máxima posible.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import vise.hooks.experience_index_builder as bld
import vise.hooks.experience_injector as inj


def _entry(pattern: str, *, kws: list[str], conf: float, desc: str) -> dict:
    return {
        "file_pattern": pattern,
        "keywords": kws,
        "domain": "general",
        "confidence": conf,
        "description": desc,
        "resolution": "r",
        "occurrences": 1,
    }


def _store(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "store.json"
    p.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return p


def _cold(entries: list[dict], target: str) -> list[str]:
    """Lo que el escaneo completo ofrece, en orden."""
    return _rank(entries, target)


def _warm(tmp_path: Path, entries: list[dict], target: str) -> list[str]:
    """Lo que la ruta indexada ofrece, en orden."""
    idx = tmp_path / "idx"
    bld.build(_store(tmp_path, entries), idx)
    score, _detail = inj._load_index_candidates(idx, str(Path(target).parent))
    return _rank(score, target)


def _rank(entries: list[dict], target: str) -> list[str]:
    kws = set(inj._extract_keywords(target))
    domain = inj._guess_domain(target)
    parent = str(Path(target).parent)
    scored = [
        (inj._score_entry(e, target, kws, domain, parent), e)
        for e in entries
    ]
    scored = [(s, e) for s, e in scored if s > 0.10]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [f"{s:.2f}" for s, _e in scored[:3]]


TARGET = "src/vise/cli/run_cmd.py"


# ---------------------------------------------------------------------------
# la regresión
# ---------------------------------------------------------------------------

def test_a_pattern_anchored_at_an_ancestor_reaches_the_query(tmp_path: Path):
    """`src/vise/*` hace fullmatch contra `src/vise/cli/run_cmd.py`."""
    entries = [_entry("src/vise/*", kws=["cli"], conf=0.9, desc="ancho")]
    idx = tmp_path / "idx"
    bld.build(_store(tmp_path, entries), idx)

    score, _ = inj._load_index_candidates(idx, "src/vise/cli")

    assert len(score) == 1, (
        "el patrón que mejor puntúa vive en el bucket del ancestro y la "
        "consulta no lo abría"
    )


def test_cold_and_warm_agree_when_an_ancestor_pattern_wins(tmp_path: Path):
    entries = [
        _entry("src/vise/*", kws=["cli"], conf=0.9, desc="ancho"),
        _entry("src/vise/cli/*.py", kws=["run"], conf=0.5, desc="mismo parent"),
        _entry("*.py", kws=["run"], conf=0.4, desc="raiz"),
    ]
    assert _warm(tmp_path, entries, TARGET) == _cold(entries, TARGET)


def test_an_entry_without_a_pattern_reaches_the_query(tmp_path: Path):
    """El builder escribía el bucket `_nopattern`; la consulta no lo leía."""
    entries = [_entry("", kws=["run", "cmd"], conf=0.9, desc="sin patron")]
    idx = tmp_path / "idx"
    bld.build(_store(tmp_path, entries), idx)

    score, detail = inj._load_index_candidates(idx, "src/vise/cli")

    assert len(score) == 1
    assert detail[0]["description"] == "sin patron"


def test_a_pattern_whose_parent_is_itself_a_wildcard_is_reachable(tmp_path: Path):
    """`src/*/cli/*.py` no tiene un padre literal: ningún ancestro lo nombra."""
    entries = [_entry("src/*/cli/*.py", kws=["run"], conf=0.9, desc="doble comodin")]
    idx = tmp_path / "idx"
    bld.build(_store(tmp_path, entries), idx)

    assert (idx / "score" / f"{bld.WILDCARD_KEY}.json").exists()
    score, _ = inj._load_index_candidates(idx, "src/vise/cli")
    assert len(score) == 1


# ---------------------------------------------------------------------------
# lo que el índice sigue descartando, a propósito
# ---------------------------------------------------------------------------

def test_an_unrelated_sibling_directory_is_still_excluded(tmp_path: Path):
    """`src/otro/*.py` no puede tocar `src/vise/cli/...`: no es ancestro ni padre."""
    entries = [_entry("src/otro/*.py", kws=["run"], conf=0.9, desc="hermano")]
    idx = tmp_path / "idx"
    bld.build(_store(tmp_path, entries), idx)

    score, _ = inj._load_index_candidates(idx, "src/vise/cli")
    assert score == [], "cargar hermanos devolvería el índice al escaneo completo"


# ---------------------------------------------------------------------------
# _bucket_keys — la forma del conjunto que se carga
# ---------------------------------------------------------------------------

def test_the_ancestor_chain_is_walked_innermost_first():
    keys = inj._bucket_keys("src/vise/cli")
    assert keys[:4] == ["P_src_vise_cli", "P_src_vise", "P_src", "P_DOT"]


def test_the_always_loaded_buckets_are_present_and_not_duplicated():
    keys = inj._bucket_keys(".")
    assert keys.count("P_DOT") == 1, "'.' ya es el último eslabón de la cadena"
    assert inj.NOPATTERN_KEY in keys
    assert inj.WILDCARD_KEY in keys


def test_an_absolute_target_still_loads_the_root_bucket():
    """`*.py` hace match contra `/home/user/x.py`, y la cadena absoluta termina en `/`."""
    keys = inj._bucket_keys("/home/user")
    assert "P_DOT" in keys


def test_the_bucket_set_is_bounded_by_depth_not_by_store_size():
    shallow = inj._bucket_keys("src")
    deep = inj._bucket_keys("a/b/c/d/e/f")
    assert len(shallow) < len(deep)
    assert len(deep) == 6 + 3  # cadena de 6 + P_DOT + nopattern + wildcard


# ---------------------------------------------------------------------------
# builder/reader tienen que estar de acuerdo o el índice se lee stale
# ---------------------------------------------------------------------------

def test_the_two_modules_agree_on_the_schema_version():
    assert inj.SCHEMA_VERSION == bld.SCHEMA_VERSION


def test_the_two_modules_agree_on_the_always_loaded_bucket_names():
    assert inj.NOPATTERN_KEY == bld.NOPATTERN_KEY
    assert inj.WILDCARD_KEY == bld.WILDCARD_KEY


def test_an_index_built_under_the_old_schema_is_not_read(tmp_path: Path):
    """El bucketing cambió: leer un índice viejo reintroduce el bug."""
    idx = tmp_path / "idx"
    store = _store(tmp_path, [_entry("src/vise/*", kws=["cli"], conf=0.9, desc="x")])
    bld.build(store, idx)

    meta = json.loads((idx / "meta.json").read_bytes())
    meta["schema_version"] = 2
    (idx / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    assert not inj._index_is_fresh(idx, store)


@pytest.mark.parametrize("pattern,expected", [
    ("", "P__nopattern"),
    ("*.py", "P_DOT"),
    ("src/vise/*.py", "P_src_vise"),
    ("src/*/cli/*.py", "P__wildcard"),
])
def test_bucket_key_routing(pattern: str, expected: str):
    assert bld.bucket_key(pattern) == expected


def test_the_entry_keeps_its_true_parent_even_when_bucketed_elsewhere(tmp_path: Path):
    """El bucket es dónde se guarda; `_parent` es el fallback de 0.7. No son lo mismo."""
    idx = tmp_path / "idx"
    bld.build(_store(tmp_path, [_entry("src/*/cli/*.py", kws=["r"], conf=0.5, desc="x")]), idx)

    score, _ = inj._load_index_candidates(idx, "src/vise/cli")
    assert score[0]["_parent"] == "src/*/cli"
