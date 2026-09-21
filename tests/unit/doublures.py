"""Doublures de test partagées par les tests unitaires."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.llm_client import Bundle, ErreurLLM, ReponseLLM, reponse_de_repli


def _repli_json(prompt: str) -> str:
    return reponse_de_repli(prompt, json_mode=True)


class FauxClient:
    """Remplace ``LLMClient`` : réponses scriptées, aucun réseau, appels comptés.

    * ``repondre(prompt) -> str`` fabrique le texte renvoyé par le « modèle »
      (par défaut : le repli déterministe par mots-clés de ``llm_client``) ;
    * ``erreur`` est levée à chaque appel si elle est fournie ;
    * ``delai_s`` simule la latence du fournisseur (tests de parallélisme).
    """

    def __init__(
        self,
        bundle: Bundle | None = None,
        *,
        repondre: Callable[[str], str] = _repli_json,
        erreur: ErreurLLM | None = None,
        delai_s: float = 0.0,
        tokens: int = 100,
    ) -> None:
        self.bundle = bundle or Bundle.charger("v2")
        self._repondre = repondre
        self._erreur = erreur
        self._delai_s = delai_s
        self._tokens = tokens
        self._verrou = threading.Lock()
        self.prompts: list[str] = []
        self.json_modes: list[bool] = []

    @property
    def appels(self) -> int:
        return len(self.prompts)

    def completer(self, prompt_utilisateur: str, *, json_mode: bool = False) -> ReponseLLM:
        with self._verrou:
            self.prompts.append(prompt_utilisateur)
            self.json_modes.append(json_mode)
        if self._delai_s:
            time.sleep(self._delai_s)
        if self._erreur is not None:
            raise self._erreur
        return ReponseLLM(
            texte=self._repondre(prompt_utilisateur),
            latence_ms=1.0,
            tokens_entree=self._tokens // 2,
            tokens_sortie=self._tokens - self._tokens // 2,
        )

    def cout_eur(self, reponse: ReponseLLM) -> float:
        return round(reponse.tokens / 1000 * self.bundle.cout_par_1k_tokens, 6)
