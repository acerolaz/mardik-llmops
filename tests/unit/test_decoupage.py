"""Tests unitaires — découpage d'un contrat en sections."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline.decoupage import Section, decouper

CONTRATS = Path(__file__).resolve().parents[2] / "eval" / "contrats"


@pytest.mark.parametrize(
    "intitule",
    [
        "Article 3 — Résiliation",
        "ARTICLE 3 : Résiliation",
        "Article 3 - Résiliation",
        "3. Résiliation",
    ],
)
def test_reconnait_les_formats_d_intitule(intitule):
    texte = f"Préambule du contrat.\n{intitule}\nLe contrat peut être résilié.\n"
    sections = decouper(texte, taille_max=60)
    assert [s.titre for s in sections] == ["préambule", intitule]


def test_texte_sans_intitule_forme_un_preambule():
    texte = "Un contrat sans aucun article numéroté."
    assert decouper(texte) == [Section(indice=0, titre="préambule", texte=texte)]


def test_pas_de_preambule_si_le_texte_commence_par_un_article():
    texte = "Article 1 — Objet\nTexte.\nArticle 2 — Durée\nTexte.\n"
    sections = decouper(texte, taille_max=25)
    assert [s.titre for s in sections] == ["Article 1 — Objet", "Article 2 — Durée"]


@pytest.mark.parametrize("cid", [f"c{i:02d}" for i in range(1, 13)])
def test_aucun_caractere_perdu_et_taille_respectee(cid):
    texte = (CONTRATS / f"{cid}.txt").read_text(encoding="utf-8")
    sections = decouper(texte)
    assert "".join(s.texte for s in sections) == texte
    assert all(len(s.texte) <= 6000 for s in sections)
    assert [s.indice for s in sections] == list(range(len(sections)))


def test_article_long_coupe_en_fin_de_phrase():
    texte = "Article 1 — Objet\n" + "Le prestataire exécute la mission avec soin. " * 10
    sections = decouper(texte, taille_max=100)
    assert len(sections) > 1
    assert "".join(s.texte for s in sections) == texte
    assert all(s.texte.endswith(".") for s in sections[:-1])


def test_phrase_geante_coupee_sans_perte():
    texte = "mot " * 400
    sections = decouper(texte, taille_max=500)
    assert len(sections) > 1
    assert "".join(s.texte for s in sections) == texte
    assert all(len(s.texte) <= 500 for s in sections)


def test_blanc_en_debut_de_fenetre_sert_de_coupe():
    texte = " " + "x" * 1000
    sections = decouper(texte, taille_max=500)
    assert len(sections) > 1
    assert sections[0].texte == " "
    assert "".join(s.texte for s in sections) == texte
    assert all(len(s.texte) <= 500 for s in sections)


def test_regroupe_les_articles_consecutifs_et_suffixe_le_titre():
    texte = "".join(f"Article {i} — Titre {i}\nCourt.\n" for i in range(1, 6))
    sections = decouper(texte)
    assert len(sections) == 1
    assert sections[0].titre == "Article 1 — Titre 1 (+4)"


def test_contrat_court_regroupe_en_peu_de_sections():
    texte = (CONTRATS / "c01.txt").read_text(encoding="utf-8")
    assert len(decouper(texte)) <= 3


def test_contrat_long_decoupe_en_plusieurs_sections():
    texte = (CONTRATS / "c12.txt").read_text(encoding="utf-8")
    assert 1 < len(decouper(texte)) <= 40
