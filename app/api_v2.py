"""Contrat ``/v2`` — la nouvelle version.

    POST /v2/analyse   {"texte": "<contrat>", "contrat_id": "c07" (optionnel)}
    → 200 {
        "clauses": [{"type": "résiliation", "extrait": "...", "confiance": 0.91,
                     "sections": [3]}, ...],
        "confiance_globale": 0.87,
        "modele": "...", "version": "v2.0.0",
        "model_version": "v2.0.0-3f9a1c2b7d10",   # version + empreinte du bundle
        "sections": 14, "appels_llm": 14,
        "latence_ms": 5230.4, "cout_eur": 0.031,
        "warnings": ["clause « garantie » : confiance 0,42 < 0,60 — relecture conseillée"]
      }
    → 413 document au-delà des limites absolues du bundle (détail explicite)
    → 422 corps invalide (détail explicite)
    → 503 fournisseur LLM indisponible (détail explicite)
    Jamais de 500 brut : toute erreur est explicite et journalisée.

Aucune troncature : le contrat passe par ``pipeline.decouper``, chaque section
par ``pipeline.extraire`` (map, en parallèle), puis ``pipeline.consolider``
(reduce) et ``pipeline.scorer``. ``analyser_v2`` est réutilisable hors HTTP (le
gate d'évaluation l'appelle directement).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import context as otel_context
from pydantic import BaseModel, Field

from app.llm_client import Bundle, ErreurLLM, LLMClient, ReponseLLM
from app.pipeline import Clause, DocumentTropLong, Section, consolider, decouper, extraire, scorer
from app.telemetry import Mesure, Telemetry, build_default_telemetry

router = APIRouter(prefix="/v2", tags=["v2"])
VERSION_V2 = "v2"
ROUTE = "/v2/analyse"


class RequeteAnalyseV2(BaseModel):
    texte: str = Field(..., min_length=20, description="Texte intégral du contrat")
    contrat_id: str | None = None


class ClauseV2(BaseModel):
    type: str
    extrait: str
    confiance: float
    sections: list[int]


class ReponseAnalyseV2(BaseModel):
    clauses: list[ClauseV2]
    confiance_globale: float
    modele: str
    version: str
    model_version: str
    sections: int
    appels_llm: int
    latence_ms: float
    cout_eur: float
    warnings: list[str]


def get_bundle_v2() -> Bundle:
    return Bundle.charger(VERSION_V2)


def get_client_v2(bundle: Bundle = Depends(get_bundle_v2)) -> LLMClient:
    return LLMClient(bundle)


def get_telemetry() -> Telemetry:
    return build_default_telemetry()


def analyser_v2(texte: str, client: LLMClient, telemetry: Telemetry) -> ReponseAnalyseV2:
    """Le cœur de la v2, réutilisable hors HTTP (tests, gate d'évaluation)."""
    bundle = client.bundle
    model_version = f"{bundle.version}-{bundle.empreinte()}"
    debut = time.perf_counter()
    with telemetry.tracer.start_as_current_span("analyse.requete") as span:
        span.set_attribute("mardik.version", bundle.version)
        span.set_attribute("mardik.model_version", model_version)
        try:
            sections = _decouper_dans_les_limites(texte, bundle.parametres)
            span.set_attribute("mardik.sections", len(sections))
            resultats = _extraire_en_parallele(sections, client, telemetry)
        except DocumentTropLong as exc:
            _enregistrer_echec(telemetry, bundle, debut)
            telemetry.logger.warning("analyse.refusee", version=bundle.version, cause=str(exc))
            raise
        except ErreurLLM as exc:
            _enregistrer_echec(telemetry, bundle, debut)
            telemetry.logger.error("analyse.echec", version=bundle.version, cause=str(exc))
            raise

        clauses, globale = scorer(consolider([c for c, _ in resultats]), texte)
        globale = round(globale, 3)
        reponses = [r for _, r in resultats]
        ignorees = [
            (s.titre, r.metadonnees["clauses_ignorees"])
            for s, r in zip(sections, reponses)
            if r.metadonnees.get("clauses_ignorees")
        ]
        warnings = construire_warnings(
            clauses, ignorees, float(bundle.parametres.get("seuil_relecture", 0.6))
        )
        cout = round(sum(client.cout_eur(r) for r in reponses), 6)
        tokens = sum(r.tokens for r in reponses)
        latence = (time.perf_counter() - debut) * 1000
        span.set_attribute("mardik.confiance_globale", globale)
        telemetry.metriques.enregistrer(
            Mesure(
                ts=time.time(),
                version=bundle.version,
                route=ROUTE,
                latence_ms=latence,
                score=globale,
                cout_eur=cout,
                appels_llm=len(sections),
                tokens=tokens,
            )
        )
        telemetry.logger.info(
            "analyse.terminee",
            version=bundle.version,
            latence_ms=round(latence, 1),
            sections=len(sections),
            clauses=len(clauses),
            confiance_globale=globale,
        )
    return ReponseAnalyseV2(
        clauses=[ClauseV2(**c.to_dict()) for c in clauses],
        confiance_globale=globale,
        modele=bundle.modele,
        version=bundle.version,
        model_version=model_version,
        sections=len(sections),
        appels_llm=len(sections),
        latence_ms=round(latence, 1),
        cout_eur=cout,
        warnings=warnings,
    )


def construire_warnings(
    clauses: list[Clause], ignorees: list[tuple[str, int]], seuil: float
) -> list[str]:
    """Les alertes lisibles par le juriste : quand relire, et pourquoi."""
    warnings: list[str] = []
    if not clauses:
        warnings.append("aucune clause détectée — relecture conseillée")
    for clause in clauses:
        if clause.confiance < seuil:
            warnings.append(
                f"clause « {clause.type} » : confiance {_fr(clause.confiance)} < {_fr(seuil)}"
                " — relecture conseillée"
            )
    total = sum(n for _, n in ignorees)
    if total:
        titres = ", ".join(dict.fromkeys(titre for titre, _ in ignorees))
        warnings.append(
            f"{total} clause(s) ignorée(s) : réponse LLM hors schéma (sections : {titres})"
        )
    return warnings


def _fr(valeur: float) -> str:
    return f"{valeur:.2f}".replace(".", ",")


def _milliers(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _decouper_dans_les_limites(texte: str, parametres: dict[str, Any]) -> list[Section]:
    """Découpe le contrat ; lève ``DocumentTropLong`` avant tout appel LLM si besoin."""
    taille_max_document = int(parametres.get("taille_max_document", 200_000))
    if len(texte) > taille_max_document:
        raise DocumentTropLong(
            f"document de {_milliers(len(texte))} caractères, limite "
            f"{_milliers(taille_max_document)} : découpez le contrat ou contactez le support"
        )
    sections = decouper(texte, int(parametres.get("contexte_max_caracteres", 6000)))
    sections_max = int(parametres.get("sections_max", 40))
    if len(sections) > sections_max:
        raise DocumentTropLong(
            f"document découpé en {len(sections)} sections, limite {sections_max} : "
            "découpez le contrat ou contactez le support"
        )
    return sections


def _extraire_en_parallele(
    sections: list[Section], client: LLMClient, telemetry: Telemetry
) -> list[tuple[list[Clause], ReponseLLM]]:
    """Un appel LLM par section, en parallèle ; résultats dans l'ordre des sections."""
    demande = int(client.bundle.parametres.get("parallelisme", 8))
    parallelisme = max(1, min(demande, len(sections)))
    contexte = otel_context.get_current()  # pour rattacher les spans llm.appel à la requête

    def tache(section: Section) -> tuple[list[Clause], ReponseLLM]:
        jeton = otel_context.attach(contexte)
        try:
            return _extraire_trace(section, client, telemetry)
        finally:
            otel_context.detach(jeton)

    with ThreadPoolExecutor(max_workers=parallelisme) as pool:
        futures = [pool.submit(tache, section) for section in sections]
        try:
            return [f.result() for f in futures]
        except ErreurLLM:
            for f in futures:
                f.cancel()
            raise


def _extraire_trace(
    section: Section, client: LLMClient, telemetry: Telemetry
) -> tuple[list[Clause], ReponseLLM]:
    with telemetry.tracer.start_as_current_span("llm.appel") as span:
        span.set_attribute("llm.section", section.indice)
        clauses, reponse = extraire(section, client)
        ignorees = int(reponse.metadonnees.get("clauses_ignorees", 0))
        span.set_attribute("llm.latence_ms", reponse.latence_ms)
        span.set_attribute("llm.tokens", reponse.tokens)
        span.set_attribute("extraction.clauses_ignorees", ignorees)
        if ignorees:
            telemetry.logger.warning(
                "extraction.clauses_ignorees", section=section.titre, nombre=ignorees
            )
        return clauses, reponse


def _enregistrer_echec(telemetry: Telemetry, bundle: Bundle, debut: float) -> None:
    telemetry.metriques.enregistrer(
        Mesure(
            ts=time.time(),
            version=bundle.version,
            route=ROUTE,
            latence_ms=(time.perf_counter() - debut) * 1000,
            erreur=True,
        )
    )


@router.post("/analyse", response_model=ReponseAnalyseV2)
def analyse(
    requete: RequeteAnalyseV2,
    client: LLMClient = Depends(get_client_v2),
    telemetry: Telemetry = Depends(get_telemetry),
) -> ReponseAnalyseV2:
    try:
        return analyser_v2(requete.texte, client, telemetry)
    except DocumentTropLong as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ErreurLLM as exc:
        raise HTTPException(
            status_code=503, detail=f"fournisseur LLM indisponible : {exc}"
        ) from exc
