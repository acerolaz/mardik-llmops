"""Client LLM unique — [FOURNI], ce n'est pas l'objet du brief.

Le client lit le *bundle* de version actif (``models/<version>/config.yaml``)
et appelle le fournisseur configuré dans ``.env`` :

* ``LLM_PROVIDER=azure``  → API hébergée type Azure AI Inference (chat/completions)
* ``LLM_PROVIDER=ollama`` → Ollama local avec un petit modèle open source

Dans les deux cas l'appel passe **par le proxy de dérive** (``LLM_PROXY_URL``,
cf. ``ops/drift_proxy.py``) : le proxy est transparent quand ``DRIFT=off``.

Quand ``MOCK=on`` (réservé à la CI), aucun appel réseau n'est fait : le client
rejoue une réponse enregistrée dans ``eval/fixtures/`` (clé = empreinte du
prompt). Si aucune fixture ne correspond, une réponse déterministe de repli
est construite à partir du texte du prompt, pour que la CI reste verte même si
le prompt d'une équipe diffère de celui qui a servi à l'enregistrement.
``MOCK=record`` appelle le vrai fournisseur **et** enregistre la réponse.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_MODELES = RACINE / "models"
DOSSIER_FIXTURES = RACINE / "eval" / "fixtures"

# Types de clauses que l'on cherche dans un contrat (vocabulaire partagé
# entre les prompts, les annotations ``eval/attendus.jsonl`` et le repli MOCK).
TYPES_CLAUSES: dict[str, list[str]] = {
    "résiliation": ["résiliation", "résilier", "résilié"],
    "pénalité de retard": ["pénalité de retard", "pénalités de retard", "retard de livraison"],
    "confidentialité": ["confidentialité", "confidentiel"],
    "propriété intellectuelle": ["propriété intellectuelle", "droits d'auteur"],
    "limitation de responsabilité": ["limitation de responsabilité", "responsabilité est limitée"],
    "force majeure": ["force majeure"],
    "durée": ["durée du contrat", "entre en vigueur", "est conclu pour une durée"],
    "prix et paiement": ["prix", "paiement", "facturation"],
    "non-concurrence": ["non-concurrence"],
    "garantie": ["garantie", "garantit"],
    "données personnelles": ["données personnelles", "rgpd"],
    "droit applicable": ["droit applicable", "droit français", "tribunaux compétents"],
    "exclusivité": ["exclusivité", "exclusif"],
    "reconduction tacite": ["reconduction tacite", "tacitement reconduit"],
}


class ErreurLLM(RuntimeError):
    """Le fournisseur n'a pas répondu correctement (HTTP, délai, réponse vide)."""


@dataclass
class Bundle:
    """Une *version du modèle* = un bundle de configuration versionné."""

    version: str
    modele: str
    prompt: str
    parametres: dict[str, Any]
    schema_sortie: dict[str, Any] | None
    strategie: str
    cout_par_1k_tokens: float = 0.0
    chemin: Path | None = None

    @classmethod
    def charger(cls, version: str, racine: Path = DOSSIER_MODELES) -> "Bundle":
        """Charge ``<racine>/<version>/config.yaml`` (``models/v1``, ``models/v2``…)."""
        return cls.charger_chemin(racine / version / "config.yaml")

    @classmethod
    def charger_chemin(cls, chemin: Path, version: str = "") -> "Bundle":
        chemin = Path(chemin)
        if not chemin.exists():
            raise FileNotFoundError(f"bundle introuvable : {chemin}")
        data = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
        prompt = data.get("prompt") or ""
        if data.get("prompt_fichier"):
            prompt = (chemin.parent / data["prompt_fichier"]).read_text(encoding="utf-8")
        modele = os.path.expandvars(str(data.get("modele", "") or "${LLM_MODEL}"))
        if "${" in modele:  # variable non définie dans l'environnement
            modele = _env("LLM_MODEL", "llama3.2:3b")
        return cls(
            version=str(version or data.get("version") or chemin.parent.name),
            modele=modele,
            prompt=prompt,
            parametres=dict(data.get("parametres") or {}),
            schema_sortie=data.get("schema_sortie"),
            strategie=str(data.get("strategie", "monolithique")),
            cout_par_1k_tokens=float(data.get("cout_par_1k_tokens", 0.0)),
            chemin=chemin,
        )

    def empreinte(self) -> str:
        """Empreinte stable du bundle (sert d'identité d'artefact au registre).

        Calculée sur le fichier ``config.yaml`` tel quel (donc indépendante de
        l'environnement qui résout ``${LLM_MODEL}``).
        """
        if self.chemin is not None and self.chemin.exists():
            return hashlib.sha256(self.chemin.read_bytes()).hexdigest()[:12]
        brut = json.dumps(
            {
                "modele": self.modele,
                "prompt": self.prompt,
                "parametres": self.parametres,
                "schema_sortie": self.schema_sortie,
                "strategie": self.strategie,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:12]


@dataclass
class ReponseLLM:
    texte: str
    latence_ms: float
    tokens_entree: int = 0
    tokens_sortie: int = 0
    mock: bool = False
    metadonnees: dict[str, Any] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return self.tokens_entree + self.tokens_sortie

    def json(self) -> Any:
        """Extrait le premier objet/tableau JSON du texte (tolère les ``` fences)."""
        return extraire_json(self.texte)


def extraire_json(texte: str) -> Any:
    texte = texte.strip()
    texte = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte, flags=re.IGNORECASE | re.MULTILINE)
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        pass
    for ouvrant, fermant in (("{", "}"), ("[", "]")):
        debut, fin = texte.find(ouvrant), texte.rfind(fermant)
        if debut != -1 and fin > debut:
            try:
                return json.loads(texte[debut : fin + 1])
            except json.JSONDecodeError:
                continue
    # Pas d'extrait : la réponse recopie souvent le contrat, et le message part dans Sentry.
    raise ErreurLLM(f"réponse LLM non-JSON ({len(texte)} caractères)")


def _env(nom: str, defaut: str = "") -> str:
    return os.environ.get(nom, defaut).strip()


def mode_mock() -> str:
    """``off`` (défaut), ``on`` (rejeu des fixtures) ou ``record``."""
    return _env("MOCK", "off").lower() or "off"


class LLMClient:
    """Le client unique : un bundle, un fournisseur, un proxy."""

    def __init__(
        self,
        bundle: Bundle,
        *,
        provider: str | None = None,
        proxy_url: str | None = None,
        timeout_s: float | None = None,
        mock: str | None = None,
        fixtures: Path = DOSSIER_FIXTURES,
    ) -> None:
        self.bundle = bundle
        self.provider = (provider or _env("LLM_PROVIDER", "ollama")).lower()
        self.proxy_url = (proxy_url or _env("LLM_PROXY_URL", "http://localhost:8080")).rstrip("/")
        self.timeout_s = timeout_s or float(_env("LLM_TIMEOUT_S", "60"))
        self.mock = (mock or mode_mock()).lower()
        self.fixtures = fixtures

    # ------------------------------------------------------------------ public
    def completer(self, prompt_utilisateur: str, *, json_mode: bool = False) -> ReponseLLM:
        """Envoie ``bundle.prompt`` (système) + ``prompt_utilisateur`` et renvoie la réponse.

        ``json_mode=True`` demande au fournisseur une sortie JSON (si supporté) et
        ajoute le ``schema_sortie`` du bundle au prompt système.
        """
        prompt_systeme = self.bundle.prompt
        if json_mode and self.bundle.schema_sortie:
            prompt_systeme += "\n\nRéponds uniquement avec un JSON valide conforme à ce schéma :\n"
            prompt_systeme += json.dumps(self.bundle.schema_sortie, ensure_ascii=False)

        if self.mock == "on":
            return self._rejouer_fixture(prompt_systeme, prompt_utilisateur)

        debut = time.perf_counter()
        texte, usage = self._appeler(prompt_systeme, prompt_utilisateur, json_mode)
        latence = (time.perf_counter() - debut) * 1000
        reponse = ReponseLLM(
            texte=texte,
            latence_ms=latence,
            tokens_entree=int(usage.get("prompt_tokens", 0)),
            tokens_sortie=int(usage.get("completion_tokens", 0)),
        )
        if self.mock == "record":
            self._enregistrer_fixture(prompt_systeme, prompt_utilisateur, reponse)
        return reponse

    def cout_eur(self, reponse: ReponseLLM) -> float:
        return round(reponse.tokens / 1000 * self.bundle.cout_par_1k_tokens, 6)

    # --------------------------------------------------------------- providers
    def _poster(self, http: httpx.Client, url: str, **kwargs: Any) -> httpx.Response:
        """POST avec réessai sur HTTP 429 (quota fournisseur dépassé).

        Attend ``Retry-After`` si le fournisseur l'envoie (plafonné), sinon
        2, 4, 8 s + aléa. Les 5xx ne sont pas réessayés : le proxy de dérive en
        injecte pour déclencher le rollback, il ne faut pas les masquer.
        """
        reessais = max(0, int(_env("LLM_RETRY_429_MAX", "3")))
        plafond = max(0.0, float(_env("LLM_RETRY_429_ATTENTE_MAX_S", "30")))
        for essai in range(reessais + 1):
            r = http.post(url, **kwargs)
            if r.status_code != 429 or essai == reessais:
                return r
            r.close()
            try:
                attente = float(r.headers["retry-after"])
            except (KeyError, ValueError):
                attente = 2 ** (essai + 1) + random.uniform(0, 1)
            time.sleep(min(attente, plafond))
        return r

    def _appeler(
        self, prompt_systeme: str, prompt_utilisateur: str, json_mode: bool
    ) -> tuple[str, dict[str, Any]]:
        params = self.bundle.parametres
        messages = [
            {"role": "system", "content": prompt_systeme},
            {"role": "user", "content": prompt_utilisateur},
        ]
        try:
            with httpx.Client(timeout=self.timeout_s) as http:
                if self.provider == "azure":
                    body: dict[str, Any] = {
                        "model": self.bundle.modele,
                        "messages": messages,
                        "temperature": params.get("temperature", 0.0),
                        "max_tokens": params.get("max_tokens", 1024),
                    }
                    if params.get("seed") is not None:
                        body["seed"] = params["seed"]
                    if json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = self._poster(
                        http,
                        f"{self.proxy_url}/chat/completions",
                        json=body,
                        headers={
                            "api-key": _env("AZURE_AI_INFERENCE_API_KEY"),
                            "Authorization": f"Bearer {_env('AZURE_AI_INFERENCE_API_KEY')}",
                            "x-mardik-provider": "azure",
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    texte = data["choices"][0]["message"]["content"] or ""
                    return texte, data.get("usage", {})

                # ollama
                body = {
                    "model": self.bundle.modele,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": params.get("temperature", 0.0),
                        "num_predict": params.get("max_tokens", 1024),
                        "seed": params.get("seed", 0),
                    },
                }
                if json_mode:
                    body["format"] = "json"
                r = self._poster(
                    http,
                    f"{self.proxy_url}/api/chat",
                    json=body,
                    headers={"x-mardik-provider": "ollama"},
                )
                r.raise_for_status()
                data = r.json()
                texte = data.get("message", {}).get("content", "") or ""
                usage = {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                }
                return texte, usage
        except httpx.HTTPStatusError as exc:
            raise ErreurLLM(
                f"fournisseur {self.provider} : HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ErreurLLM(f"fournisseur {self.provider} injoignable : {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise ErreurLLM(f"réponse inattendue du fournisseur : {exc}") from exc

    # ---------------------------------------------------------------- fixtures
    def _cle_fixture(self, prompt_systeme: str, prompt_utilisateur: str) -> str:
        brut = f"{self.bundle.modele}\n{prompt_systeme}\n---\n{prompt_utilisateur}"
        return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:20]

    def _rejouer_fixture(self, prompt_systeme: str, prompt_utilisateur: str) -> ReponseLLM:
        cle = self._cle_fixture(prompt_systeme, prompt_utilisateur)
        chemin = self.fixtures / f"{cle}.json"
        if chemin.exists():
            data = json.loads(chemin.read_text(encoding="utf-8"))
            return ReponseLLM(
                texte=data["texte"],
                latence_ms=float(data.get("latence_ms", 5.0)),
                tokens_entree=int(data.get("tokens_entree", 0)),
                tokens_sortie=int(data.get("tokens_sortie", 0)),
                mock=True,
                metadonnees={"fixture": chemin.name},
            )
        texte = reponse_de_repli(prompt_utilisateur, json_mode=bool(self.bundle.schema_sortie))
        return ReponseLLM(
            texte=texte,
            latence_ms=5.0,
            tokens_entree=len(prompt_systeme + prompt_utilisateur) // 4,
            tokens_sortie=len(texte) // 4,
            mock=True,
            metadonnees={"fixture": None, "repli": True},
        )

    def _enregistrer_fixture(
        self, prompt_systeme: str, prompt_utilisateur: str, reponse: ReponseLLM
    ) -> None:
        self.fixtures.mkdir(parents=True, exist_ok=True)
        cle = self._cle_fixture(prompt_systeme, prompt_utilisateur)
        (self.fixtures / f"{cle}.json").write_text(
            json.dumps(
                {
                    "modele": self.bundle.modele,
                    "version": self.bundle.version,
                    "texte": reponse.texte,
                    "latence_ms": round(reponse.latence_ms, 1),
                    "tokens_entree": reponse.tokens_entree,
                    "tokens_sortie": reponse.tokens_sortie,
                    "extrait_prompt": prompt_utilisateur[:200],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def reponse_de_repli(texte: str, *, json_mode: bool) -> str:
    """Réponse déterministe construite par mots-clés (mode MOCK sans fixture).

    Ce n'est pas un modèle : c'est un filet de sécurité pour la CI. Le texte
    analysé est celui qui est passé dans le prompt utilisateur — donc si le
    contrat a été tronqué avant l'appel (v1), les clauses de la fin manquent,
    exactement comme avec le vrai modèle.
    """
    minuscule = texte.lower()
    trouvees: list[dict[str, Any]] = []
    for type_clause, mots in TYPES_CLAUSES.items():
        for mot in mots:
            pos = minuscule.find(mot)
            if pos != -1:
                debut = max(0, texte.rfind("\n", 0, pos) + 1)
                fin = texte.find("\n", pos)
                extrait = texte[debut : fin if fin != -1 else len(texte)].strip()[:240]
                trouvees.append(
                    {"type": type_clause, "extrait": extrait, "confiance": 0.86}
                )
                break
    if json_mode:
        return json.dumps({"clauses": trouvees}, ensure_ascii=False)
    return "\n".join(f"- {c['type']}" for c in trouvees) or "Aucune clause identifiée."
