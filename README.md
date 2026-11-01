# Voix des Douze

Application **macOS locale** : une fenêtre Dofus choisie → OCR Apple Vision français → voix Piper CPU persistante par PNJ. Sans LLM, compte, API payante, modification du client, interception réseau ou automatisation du jeu. Projet indépendant d’Ankama et de Romandub ; aucun enregistrement de ces créateurs n’est utilisé.

## Priorité actuelle et perspective

**Objectif immédiat :** détecter l’ouverture d’un dialogue dans la seule fenêtre Dofus choisie, identifier le PNJ et récupérer le texte de sa réplique. La piste réseau passive n’est pas intégrée à l’application ; une preuve ponctuelle a été obtenue sur une session réelle de Dofus 3.7.12.12 : 149 paquets capturés, une ouverture de dialogue et cinq répliques d’Agride Shakoku résolues sans OCR. La capture a nécessité une autorisation administrateur ponctuelle, expressément accordée, sans modifier durablement les permissions. L’analyse a été faite après capture, avec des correspondances statiques inspectées manuellement dans la [publication communautaire correspondant à cette version](https://github.com/ledouxm/dofus-sqlite/releases/tag/v6.0_3.7.12.12) : ce n’est pas encore un détecteur générique en direct. Aucun proxy, envoi d’action au jeu ou changement du client. Les paquets restent locaux, hors Git. La base OCR reste disponible.

**Exercice vocal en direct ensuite réalisé :** ouverture du dialogue d’Agride Shakoku → résolution de la première réplique sans OCR → lancement réel d’afplay avec Piper/Siwis environ 1,28 s après la détection réseau. L’utilisateur a confirmé entendre la lecture. Une réponse du joueur a également été détectée et a invalidé la lecture en cours. Cet essai borné utilisait un catalogue local de noms et de répliques de la version 3.7.12.12 ; il ne constitue pas encore une intégration dans l’interface ni une validation des autres PNJ ou dialogues paramétrés. L’écoute a été arrêtée à la fin du test.

**Perspective à long terme, hors du travail immédiat :** proposer à Ankama un produit local autonome avec voix de base et choix personnel des voix par PNJ, accompagné d’un service communautaire. Les joueurs pourraient prévisualiser et valider l’envoi du texte et de l’audio, y compris des enregistrements humains autorisés, vers un serveur distant. Une interface web permettrait de voter positivement ou négativement pour les voix associées aux PNJ et d’en proposer les mieux classées par défaut, sans écraser les choix personnels. Le lecteur local resterait utilisable hors ligne après préparation ; les contributions et mises à jour communautaires nécessiteraient une connexion.

Cette perspective n’est pas implémentée et n’implique aucune affiliation ou approbation d’Ankama. Aucun clonage vocal n’est autorisé ; les droits des contributeurs, les autorisations relatives aux textes et les modalités de modération restent à définir avant publication du service.

## Installer et lancer

Prérequis : macOS 14+, **Python 3.13** et outils Apple Command Line Tools (`xcode-select --install` si absents). Testé sur M4 / 32 Go / macOS 26.5.2 / Python 3.13.8. Windows hors périmètre.

1. Double-cliquer **`Installer.command`** : crée `.venv`, installe les versions de `requirements.lock`, télécharge trois modèles français (~203 Mo), vérifie SHA256/licences et compile le moteur Swift. Aucun sudo ou paquet Python global. Installation relançable ; un modèle existant non conforme reste intact.
2. Double-cliquer **`Lancer.command`** : vraie interface PySide6 native, pas une page web. Aucun téléchargement au lancement ; préchauffage des modèles dans un worker, pendant plusieurs secondes.
3. Choisir une fenêtre puis **Démarrer**. Une seule source est surveillée, même en arrière-plan si macOS permet sa capture.

```sh
./Installer.command
./Lancer.command
./Lancer.command --demo
./Lancer.command --image /chemin/capture.png
./Tester.command
```

Si Python n’est pas trouvé : `PYTHON=/chemin/python3.13 ./Installer.command` lors de la première installation. Ne pas déplacer `.venv` seul ; relancer l’installation après déplacement du projet.

## Permission macOS

Cliquer **Autorisations…**, puis accorder l’accès dans **Réglages Système → Confidentialité et sécurité → Enregistrement de l’écran et de l’audio système**, à l’entrée présentée par macOS (application hôte ou moteur `engine-…`). Relancer ensuite et actualiser les fenêtres. Le moteur est dans `~/Library/Caches/VoixDesDouze/` ; Finder → Aller au dossier permet de le sélectionner si nécessaire.

**L’accès écran est actuellement refusé sur la machine de validation.** Images fixes et démonstration fonctionnent sans cette permission. Plein écran, arrière-plan, multi-fenêtres et redimensionnement Retina sont implémentés mais restent à valider en jeu réel. Aucun accès micro. Les raccourcis Carbon ne surveillent pas les frappes et ne demandent pas Accessibilité.

## Utiliser

- Lecture **automatique** par défaut ; décocher pour reconnaître sans parler, puis **Lire / Relire**. Suspendre coupe capture et audio ; arrêter invalide aussi les générations en attente.
- Attribution automatique mémorisée, ajustable par PNJ, voix de secours si le nom manque. Quatre choix : **Siwis**, **MLS 1840** (index 0), **MLS 3698** (index 1), **Tom**. Utiliser **Préécouter** : plusieurs signaux distincts ne garantissent pas une bonne qualité perceptive. Volume et vitesse 0,5×–2× réglables.
- Réponses reconnues séparément ; lecture désactivée par défaut, avec voix distincte si activée.
- Repérage automatique des panneaux mobiles du thème bleu nuit à cartouche beige des captures fournies. Autres rendus : **Délimiter les zones…**, puis tracer nom/dialogue/réponses. Rectangles relatifs à la fenêtre ; ils suivent son échelle, pas le déplacement d’une bulle. Recocher le repérage automatique supprime les zones manuelles de cette source.
- Aperçu actualisé aux changements des panneaux, texte OCR affiché. Stabilité de 350 ms, dédoublonnage conservateur, réouverture permettant la relecture. Un changement détecté invalide l’ancienne lecture. L’OCR peut se tromper.
- Raccourcis globaux **Contrôle + Option + F6/F7/F8** : suspendre/reprendre, arrêter, relire. Touches F1–F12 configurables ; Fn peut être nécessaire ; conflits signalés.
- Démonstration : images **synthétiques**, vrai OCR et vraies voix, changement toutes les 12 s ou bouton suivant. Ce n’est pas un test Dofus en direct.
- **Captures de test** : accès direct à Kerubim Crépin, Nacf Virgil et Al Etsop. Choisir un PNJ charge sa capture et lance la reconnaissance ; la lecture suit le réglage automatique, sinon cliquer **Lire / Relire**. Ces trois images réelles recadrées sont versionnées dans `tests/fixtures/dialogues/`, sans le chat ni les noms de compte, et replacées sur un fond neutre comme dans les benchmarks. Aucun accès écran nécessaire.

## Données, tests et limites

Réglages et voix : `~/Library/Application Support/VoixDesDouze/settings.json` (écriture atomique ; configuration illisible non écrasée). Modèles : `~/Library/Caches/VoixDesDouze/models/` ; conserver ce cache pour l’usage hors ligne. Révision et SHA256 : `models/manifest.json`. Moteur Swift compilé localement et mis en cache ; une mise à jour peut nécessiter de réaccorder sa permission. Captures et WAV de lecture temporaires, aucun corps de dialogue conservé ou envoyé en ligne.

**`RAPPORT_TESTS.md`** donne les résultats, performances et validations restantes. WAV et JSON réels dans **`samples/`**. **Pas d’écoute humaine revendiquée** : fin normale d’afplay et signal non silencieux ne prouvent pas une sortie audible ou une bonne prononciation. La suite a aussi passé sous interdiction réseau macOS.

```sh
.venv/bin/python -m pytest tests -v
.venv/bin/python scripts/prepare_models.py --verify-only
.venv/bin/python scripts/prepare_models.py --verify-only --samples-dir samples
.venv/bin/python scripts/benchmark.py --play
.venv/bin/python scripts/check_audio.py --volume 0.15
```

Les commandes de mesure régénèrent les rapports. `--play` lance brièvement afplay ; `check_audio.py` lit les quatre WAV jusqu’à la fin. Les tests OCR exigent macOS ; les tests TTS réels sont explicitement ignorés si les modèles ne sont pas préparés. Aucun test ne télécharge implicitement de modèle. Aucune performance n’est promise sur une majorité de Macs/PC.

Licences à respecter avant redistribution : [Piper GPL-3.0](https://github.com/OHF-Voice/piper1-gpl), PySide6 LGPL/GPL selon composants ; voix MLS/Siwis **CC-BY 4.0**, Tom **AGPLv3** selon les MODEL_CARD conservées dans le cache. Provenance/attribution des corpus dans le manifeste ; aucun modèle binaire dans Git.
