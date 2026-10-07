"""Vérifie le script sur une vraie réponse de l'API, enregistrée le 7 octobre 2026.

Lancement : python -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import veille

ECHANTILLON = json.loads((Path(__file__).parent / "echantillon_techeu.json").read_text(encoding="utf-8"))
CORRECTIONS = {"Besancon": "Besançon"}


class TestVeille(unittest.TestCase):
    def test_simplifier_garde_les_bons_champs(self):
        vocca = veille.simplifier(ECHANTILLON["data"][2], CORRECTIONS, "2026-10-07")
        self.assertEqual(vocca["entreprise"], "Vocca")
        self.assertEqual(vocca["ville"], "Paris")
        self.assertEqual(vocca["montant_eur"], 17747804)
        self.assertEqual(vocca["secteurs"], ["Software"])
        self.assertEqual(vocca["source"], "maddyness.com")
        self.assertEqual(vocca["lien"], "https://funding.tech.eu/deals/905FBA62-9D58-48AD-9C07-78747B4CF17F")

    def test_simplifier_corrige_la_ville(self):
        archeon = veille.simplifier(ECHANTILLON["data"][4], CORRECTIONS, "2026-10-07")
        self.assertEqual(archeon["ville"], "Besançon")

    def test_fusionner_sans_doublon_et_garde_la_premiere_date(self):
        hier = [veille.simplifier(l, CORRECTIONS, "2026-10-06") for l in ECHANTILLON["data"][:3]]
        aujourd_hui = [veille.simplifier(l, CORRECTIONS, "2026-10-07") for l in ECHANTILLON["data"]]
        levees, ajoutees = veille.fusionner(hier, aujourd_hui)
        self.assertEqual(len(levees), 5)
        self.assertEqual(ajoutees, 2)
        par_nom = {l["entreprise"]: l for l in levees}
        self.assertEqual(par_nom["Vocca"]["vue_le"], "2026-10-06")
        self.assertEqual(par_nom["Archeon"]["vue_le"], "2026-10-07")

    def test_recuperer_levees_suit_les_pages(self):
        demandes = []

        def fausse_api(url):
            demandes.append(url)
            if "after=" not in url:
                return {"data": ECHANTILLON["data"][:3], "nextCursor": "page2"}
            return {"data": ECHANTILLON["data"][3:], "nextCursor": None}

        levees = veille.recuperer_levees("France", "2026-10-01", 1000000, lire=fausse_api)
        self.assertEqual(len(levees), 5)
        self.assertEqual(len(demandes), 2)
        self.assertIn("country=France", demandes[0])
        self.assertIn("minAmount=1000000", demandes[0])
        self.assertIn("after=page2", demandes[1])


if __name__ == "__main__":
    unittest.main()
