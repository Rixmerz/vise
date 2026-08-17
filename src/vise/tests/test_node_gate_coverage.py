"""Ningún nodo queda sin verificación mecánica por accidente.

Medido sobre los 9 workflows bundleados: **46 de 49 aristas son de tipo
`phrase`** (94%) y solo 14 de 54 nodos declaraban validadores (26%). Traducido:
la mayor parte del "gating por fases" de vise era el agente afirmando que hizo
algo, no una comprobación de que lo hizo. vise se presenta como un enforcer;
en esa proporción era un asistente de disciplina.

El arreglo no es poner validadores en todos lados. Muchas fases son
genuinamente cognitivas — `understand`, `hypothesize`, `triage` — y colgarles
una comprobación falsa es peor que no tener ninguna: enseña a la gente a
exportar `VISE_NODE_GATE_OVERRIDE=1`, que es el hábito que las puertas existen
para evitar.

El criterio que se aplicó, y que este test sostiene:

    Un nodo cuya propia SIGNAL ya afirma algo mecánico tiene que
    comprobarlo. Un nodo que no, tiene que decir por qué acá.

`debug:reproduce` era el caso más claro: el nodo pide en prosa *"Confirm that
at least one test fails"* y no había forma de expresarlo, así que la puerta
más fuerte de todo el workflow de debug era una frase. De ahí salió el
validador `tests_fail`.

Lo que este test protege no es el número. Es que el próximo nodo que se agregue
sin validadores sea una decisión y no un olvido.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parents[1] / "assets" / "workflows"


# ---------------------------------------------------------------------------
# Nodos deliberadamente sin comprobación mecánica, con el motivo.
#
# Agregar una entrada acá es admitir que el nodo se cierra con el juicio del
# agente. Es una respuesta legítima; lo que no es legítimo es no responder.
# ---------------------------------------------------------------------------

_COGNITIVO = "fase de lectura y juicio: no produce artefacto que una máquina pueda leer"
_PROSA = "el artefacto es prosa para humanos; su calidad no es comprobable por exit code"
_EXTERNO = "el efecto vive fuera del repo (GitHub, red), no en el árbol de trabajo"
_PROYECTO = "comprobable en principio, pero solo contra convenciones del proyecto que vise no conoce"

UNVERIFIED_BY_DESIGN: dict[tuple[str, str], str] = {
    # --- debug ---------------------------------------------------------------
    ("debug", "understand"): _COGNITIVO,
    ("debug", "classify"): _COGNITIVO,
    ("debug", "analyze"): _COGNITIVO,
    ("debug", "hypothesize"): _COGNITIVO,
    ("debug", "strategy-tests"): _COGNITIVO,
    ("debug", "strategy-flowtrace"): _COGNITIVO,
    ("debug", "strategy-hybrid"): _COGNITIVO,
    ("debug", "unreproducible"): (
        "es la salida del caso en que NO hay reproducción; exigirle una "
        "comprobación verde sería exigir lo contrario de lo que el nodo significa"
    ),
    ("debug", "report"): _PROSA,
    # --- dogfood -------------------------------------------------------------
    ("dogfood", "run-on-self"): _COGNITIVO,
    ("dogfood", "capture-issues"): _COGNITIVO,
    ("dogfood", "triage"): _COGNITIVO,
    ("dogfood", "file"): _EXTERNO,
    # --- feature-dev ---------------------------------------------------------
    ("feature-dev", "orient"): _COGNITIVO,
    ("feature-dev", "design"): _COGNITIVO,
    ("feature-dev", "commit"): (
        "el commit ya pasó por `validate`, que sí comprueba; repetir la suite "
        "acá solo agrega latencia a un nodo que no cambia el árbol"
    ),
    # --- migration -----------------------------------------------------------
    ("migration", "design"): _COGNITIVO,
    ("migration", "apply"): (
        "aplicar la migración corre contra un sistema real (base de datos, "
        "servicio); vise no puede distinguir un fallo de aplicación de un "
        "entorno ausente, y equivocarse acá bloquea un workflow a mitad de camino"
    ),
    # --- pr-review -----------------------------------------------------------
    ("pr-review", "fetch"): _EXTERNO,
    ("pr-review", "analyze"): _COGNITIVO,
    ("pr-review", "comment"): _EXTERNO,
    # --- quality-gate --------------------------------------------------------
    ("quality-gate", "deep-passes"): (
        "las cuatro fases previas ya comprueban mecánicamente; este nodo es el "
        "lazo de profundización sobre lo que aquellas reportaron"
    ),
    # --- release -------------------------------------------------------------
    ("release", "changelog"): _PROSA,
    ("release", "version-bump"): _PROYECTO,
    ("release", "tag"): _PROYECTO,
    ("release", "notify"): _EXTERNO,
    # --- security-audit ------------------------------------------------------
    ("security-audit", "scan"): (
        "deliberadamente sin puerta: un scanner que sale distinto de cero acá "
        "significa que ENCONTRÓ algo, que es el resultado esperado del nodo. "
        "El nodo lo dice en su propio comentario"
    ),
    ("security-audit", "triage"): _COGNITIVO,
    ("security-audit", "doc"): _PROSA,
    # --- sprint-e2e ----------------------------------------------------------
    ("sprint-e2e", "orient"): _COGNITIVO,
    ("sprint-e2e", "contract"): _PROYECTO,
    ("sprint-e2e", "close"): _PROSA,
}


def _graphs() -> list[tuple[str, dict]]:
    return [
        (p.stem.removesuffix("-graph"), yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(WORKFLOWS.glob("*-graph.yaml"))
    ]


def _all_nodes() -> list[tuple[str, str, dict]]:
    return [(wf, n["id"], n) for wf, g in _graphs() for n in (g.get("nodes") or [])]


# ---------------------------------------------------------------------------
# la invariante
# ---------------------------------------------------------------------------

def test_every_node_either_verifies_or_says_why_not():
    """La regla entera, en una aserción."""
    silent = [
        (wf, nid)
        for wf, nid, n in _all_nodes()
        if not n.get("validators") and (wf, nid) not in UNVERIFIED_BY_DESIGN
    ]
    assert not silent, (
        "estos nodos no comprueban nada y tampoco declaran por qué:\n  "
        + "\n  ".join(f"{wf}:{nid}" for wf, nid in silent)
        + "\n\nAgregá validadores, o una entrada en UNVERIFIED_BY_DESIGN con el motivo."
    )


def test_the_exemption_list_has_no_stale_entries():
    """Un nodo renombrado o al que se le agregaron validadores deja basura acá."""
    real = {(wf, nid) for wf, nid, _ in _all_nodes()}
    verified = {(wf, nid) for wf, nid, n in _all_nodes() if n.get("validators")}

    ghosts = sorted(k for k in UNVERIFIED_BY_DESIGN if k not in real)
    assert not ghosts, f"exentos que ya no existen: {ghosts}"

    redundant = sorted(k for k in UNVERIFIED_BY_DESIGN if k in verified)
    assert not redundant, (
        f"exentos que SÍ comprueban — borrá la excusa: {redundant}"
    )


def test_every_exemption_states_a_reason():
    empty = sorted(k for k, v in UNVERIFIED_BY_DESIGN.items() if not (v or "").strip())
    assert not empty, f"exentos sin motivo: {empty}"


# ---------------------------------------------------------------------------
# el trinquete — que la proporción no vuelva atrás en silencio
# ---------------------------------------------------------------------------

# Subilo cuando el número real suba; nunca lo bajes para que pase un cambio.
# Es el mismo trinquete que el piso de cobertura en CLAUDE.md.
MIN_VERIFIED_NODES = 22


def test_the_number_of_mechanically_gated_nodes_does_not_regress():
    verified = [1 for _wf, _nid, n in _all_nodes() if n.get("validators")]
    assert len(verified) >= MIN_VERIFIED_NODES, (
        f"{len(verified)} nodos comprueban mecánicamente, el piso es "
        f"{MIN_VERIFIED_NODES} — se le sacaron validadores a un nodo"
    )


# ---------------------------------------------------------------------------
# los validadores declarados tienen que existir de verdad
# ---------------------------------------------------------------------------

def test_every_declared_validator_type_is_in_the_registry():
    """Un tipo con typo no es un nodo laxo: `build_validators` falla cerrado."""
    from vise.engines.validators import _REGISTRY

    unknown = sorted({
        v.get("type") or v.get("name")
        for _wf, _nid, n in _all_nodes()
        for v in (n.get("validators") or [])
        if (v.get("type") or v.get("name")) not in _REGISTRY
    })
    assert not unknown, f"tipos de validador que no existen: {unknown}"


@pytest.mark.parametrize("wf,nid", [
    ("debug", "reproduce"),
    ("debug", "verify"),
    ("sprint-e2e", "e2e"),
    ("migration", "reversibility-check"),
    ("security-audit", "fix-criticals"),
])
def test_the_nodes_whose_signal_is_a_mechanical_claim_check_it(wf: str, nid: str):
    """Los casos concretos que motivaron el cambio, fijados uno por uno."""
    node = next(n for w, i, n in _all_nodes() if (w, i) == (wf, nid))
    assert node.get("validators"), f"{wf}:{nid} afirma algo mecánico y no lo comprueba"


def test_reproduce_checks_that_a_test_actually_fails():
    """`tests_pass` acá sería exactamente al revés de lo que el nodo pide."""
    node = next(n for w, i, n in _all_nodes() if (w, i) == ("debug", "reproduce"))
    types = {v.get("type") for v in node["validators"]}
    assert "tests_fail" in types
    assert "tests_pass" not in types
