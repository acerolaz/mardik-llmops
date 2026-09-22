"""Tests d'intégration — publication d'une version (registre isolé en tmp_path)."""
from __future__ import annotations

import pytest

from app.llm_client import Bundle
from eval.run_eval import Rapport
from ops.deploy import ErreurDeploiement, publier


def _rapport(passe: bool = True, **modifs) -> Rapport:
    donnees = {
        "version": "v2.0.0",
        "date": "2026-09-22T00:00:00+00:00",
        "essais": 1,
        "note": 0.9,
        "par_contrat": {},
        "latence_p95_ms": 120.0,
        "cout_moyen_eur": 0.01,
        "passe": passe,
        "motifs": [] if passe else ["note 0.400 < seuil 0.75"],
        "seuil": 0.75,
        "mode_eval": "mock",
        "seuils": {"note_min": 0.75, "latence_p95_max_ms": 8000.0,
                   "cout_moyen_max_eur": 0.15, "motif": "m"},
        "bundle_empreinte": Bundle.charger("v2").empreinte(),
    }
    return Rapport(**{**donnees, **modifs})


def test_numerotation_automatique(registry):
    premier = publier(bundle="v2", commit="aaa1111", registry=registry, rapport=_rapport())
    second = publier(bundle="v2", commit="bbb2222", registry=registry, rapport=_rapport())
    assert (premier["version"], second["version"]) == ("v2.0.0", "v2.0.1")
    assert registry.versions() == ["v1.0.0", "v2.0.0", "v2.0.1"]


def test_versions_injectees(registry):
    manifeste = publier(
        bundle="v2", commit="c", registry=registry, rapport=_rapport(), versions={"v2.0.4"}
    )
    assert manifeste["version"] == "v2.0.5"


def test_manifeste_complet_et_journal(registry):
    manifeste = publier("v2.0.0", bundle="v2", commit="abc1234", registry=registry,
                        rapport=_rapport())
    assert manifeste["commit"] == "abc1234"
    assert manifeste["note_eval"] == 0.9
    assert manifeste["bundle_source"] == "v2"
    assert manifeste["mode_eval"] == "mock"
    assert manifeste["seuils"]["note_min"] == 0.75
    assert manifeste["latence_p95_ms"] == 120.0
    assert manifeste["cout_moyen_eur"] == 0.01
    assert manifeste["essais"] == 1
    assert registry.manifest("v2.0.0") == manifeste
    derniere = registry.journal()[-1]
    assert derniere["evenement"] == "publication"
    assert (derniere["version"], derniere["commit"], derniere["mode_eval"]) == (
        "v2.0.0", "abc1234", "mock"
    )


def test_gate_en_echec_bloque_et_se_journalise(registry):
    with pytest.raises(ErreurDeploiement, match="gate en échec : note 0.400"):
        publier(bundle="v2", commit="c", registry=registry, rapport=_rapport(passe=False))
    assert registry.versions() == ["v1.0.0"]
    refus = registry.journal()[-1]
    assert refus["evenement"] == "publication_refusee"
    assert refus["version"] == "v2.0.0"
    assert refus["motifs"] == ["note 0.400 < seuil 0.75"]


def test_rapport_d_un_autre_bundle_refuse(registry):
    with pytest.raises(ErreurDeploiement, match="rapport d'un autre bundle"):
        publier(bundle="v2", commit="c", registry=registry, rapport=_rapport(bundle_empreinte="autre"))
    assert registry.versions() == ["v1.0.0"]


def test_tag_incoherent_avec_le_bundle(registry):
    with pytest.raises(ErreurDeploiement, match="ne correspond pas"):
        publier("v3.0.0", bundle="v2", commit="c", registry=registry, rapport=_rapport())
    assert registry.versions() == ["v1.0.0"]


def test_version_en_double(registry):
    publier("v2.0.0", bundle="v2", commit="c", registry=registry, rapport=_rapport())
    with pytest.raises(ErreurDeploiement, match="déjà publiée"):
        publier("v2.0.0", bundle="v2", commit="c", registry=registry, rapport=_rapport())


def test_gate_mal_configure(registry, tmp_path, monkeypatch):
    invalide = tmp_path / "seuils.yaml"
    invalide.write_text("note_min: 0.75\n", encoding="utf-8")
    monkeypatch.setenv("SEUILS_PATH", str(invalide))
    with pytest.raises(ErreurDeploiement, match="gate mal configuré"):
        publier(bundle="v2", commit="c", registry=registry)


def test_cli_publier_depuis_un_rapport(tmp_path, monkeypatch, capsys):
    import json

    from ops import deploy

    monkeypatch.setattr(deploy, "_tags_git", lambda: {"v2.0.2"})
    monkeypatch.setattr(deploy, "_tag_de_head", lambda version_bundle: None)
    chemin = tmp_path / "rapport.json"
    chemin.write_text(json.dumps(_rapport().to_dict()), encoding="utf-8")

    assert deploy.main(["publier", "--commit", "abc1234", "--rapport", str(chemin)]) == 0
    manifeste = json.loads(capsys.readouterr().out)
    assert manifeste["version"] == "v2.0.3"
    assert manifeste["commit"] == "abc1234"
    assert manifeste["mode_eval"] == "mock"


def test_cli_relance_publie_le_tag_deja_pose_sur_head(tmp_path, monkeypatch, capsys):
    """Relance idempotente (F1c) : HEAD porte déjà un tag → on republie CETTE version."""
    import json

    from ops import deploy

    monkeypatch.setattr(deploy, "_tags_git", lambda: set())
    monkeypatch.setattr(deploy, "_tag_de_head", lambda version_bundle: "v2.0.7")
    chemin = tmp_path / "rapport.json"
    chemin.write_text(json.dumps(_rapport(version="v2.0.7").to_dict()), encoding="utf-8")

    assert deploy.main(["publier", "--commit", "abc1234", "--rapport", str(chemin)]) == 0
    manifeste = json.loads(capsys.readouterr().out)
    assert manifeste["version"] == "v2.0.7"


@pytest.mark.parametrize(
    ("contenu", "motif"),
    [
        (None, "introuvable"),
        ("{pas du json", "illisible"),
        ('{"version": "v2.0.0"}', "incomplet"),
        ("[]", "incomplet"),
        ('{"version": "v2.0.0", "passe": "false"}', "incomplet"),
    ],
)
def test_cli_rapport_invalide(tmp_path, capsys, contenu, motif):
    from ops import deploy

    chemin = tmp_path / "rapport.json"
    if contenu is not None:
        chemin.write_text(contenu, encoding="utf-8")
    assert deploy.main(["publier", "--rapport", str(chemin)]) == 1
    assert motif in capsys.readouterr().err
