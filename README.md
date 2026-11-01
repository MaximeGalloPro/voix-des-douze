# Voix des Douze

Application **macOS locale** : événements du client Dofus choisi → nom et texte du PNJ → voix françaises Piper CPU, mémorisées par personnage. Aucun OCR, capture d’écran, LLM, compte ou API payante. Aucun proxy, modification du client ou envoi d’action au jeu.

Projet indépendant d’Ankama et de Romandub, sans affiliation ou approbation supposée. La lecture passive du réseau ne vaut pas autorisation d’Ankama ; sa compatibilité contractuelle doit être clarifiée avant diffusion communautaire.

## Installer et lancer

Prérequis : macOS 14+, **Python 3.13**, et Python système des Command Line Tools Apple (`xcode-select --install` si nécessaire). Testé sur M4 / 32 Go / macOS 26.5.2. Aucune compilation Swift ni permission d’enregistrement d’écran.

1. Double-cliquer **`Installer.command`** : crée le venv isolé et prépare les modèles français (~203 Mo), versions et SHA256 figés. Aucun paquet Python global ni élévation pendant l’installation.
2. Double-cliquer **`Lancer.command`**. Au premier usage, cliquer **Préparer les données (~90 Mo)** : téléchargement explicite des textes français et métadonnées PNJ de la version vérifiée. Préparation reproductible par `scripts/prepare_network.py`.
3. Ouvrir Dofus, **Actualiser**, choisir son client (PID), puis **Démarrer l’écoute**. Seule la capture demande une autorisation administrateur **dans la fenêtre macOS**, jamais dans la conversation ni dans l’application.
4. **Changer de carte puis revenir** pour synchroniser les PNJ, puis ouvrir un dialogue. La première réplique et les suivantes sont affichées et lues automatiquement. Répondre invalide immédiatement l’ancienne lecture.
5. **Arrêter** coupe la capture et la voix ; fermer l’application fait de même. Aucun service permanent ni démarrage automatique avec le Mac.

```sh
./Installer.command
.venv/bin/python scripts/prepare_network.py
./Lancer.command
./Tester.command
```

Après préparation, l’application ne télécharge rien pour démarrer l’écoute ou produire la voix. Le jeu lui-même nécessite sa connexion habituelle. Aucun historique de paquets ou de dialogues n’est enregistré par l’application et rien n’est envoyé à un serveur communautaire.

### Raccourci dans le Dock

Après installation, exécuter `.venv/bin/python scripts/install_dock.py`. Ce script crée **`~/Applications/Voix des Douze.app`** et l’épingle au Dock, sans Terminal, sudo ou service permanent. Les autres icônes et leur ordre sont conservés ; relancer le script ne crée pas de doublon. Une sauvegarde initiale du Dock est conservée dans `~/Library/Application Support/VoixDesDouze/dock-before-shortcut.plist`.

Le lanceur utilise le venv et le code de ce dossier : ce n’est pas une distribution autonome. Garder le dossier du projet en place ; après déplacement, recréer le raccourci depuis son nouvel emplacement. Le lancement affiche l’application mais ne démarre pas l’écoute réseau.

## Contrôles et limites

- **Ce PNJ** : attribution automatique mémorisée, modifiable ; **Par défaut**, **Préécouter**, volume et vitesse 0,5×–2×. Siwis, deux locuteurs MLS et Tom disponibles. La qualité perceptive reste à apprécier en écoutant les voix.
- Décocher **Lecture automatique** pour afficher sans parler, puis **Lire / Relire**. Suspendre coupe l’écoute et la voix ; reprendre ouvre une nouvelle session et nécessite de resynchroniser la carte.
- Raccourcis globaux **Contrôle + Option + F6/F7/F8** : suspendre/reprendre, arrêter, relire ; touches configurables, Fn parfois nécessaire. Aucune frappe de jeu n’est observée ou automatisée.
- Mode expérimental limité à **Dofus 3.7.12.12**, connexion de jeu IPv4/TCP 5555 et capture Ethernet/Wi-Fi. Le build du processus choisi est vérifié dans son journal de démarrage ; une version différente est refusée.
- Les dialogues à paramètres ne sont pas encore résolus : un message explicite remplace la lecture, jamais un texte approximatif. Les choix du joueur sont détectés, mais leur texte n’est pas affiché ou lu.
- Le client choisi correspond à un processus et à son flux réseau, pas à une zone d’écran. L’isolation entre deux connexions est testée ; plusieurs comptes réels simultanés restent à valider. Le plein écran et le déplacement des panneaux n’interviennent plus dans la détection.
- Si le jeu se reconnecte ou si son flux change, l’écoute s’arrête : la relancer explicitement, puis resynchroniser la carte. Un catalogue correspondant au numéro de version ne garantit pas à lui seul que les clés obfusquées resteront stables après une mise à jour.
- Pas encore de mesure prolongée CPU/mémoire/autonomie. Les résultats effectivement observés sont dans **`RAPPORT_TESTS.md`**.

## Données et sauvegarde OCR

Réglages : `~/Library/Application Support/VoixDesDouze/settings.json`. La migration retire les anciennes zones/options de réponses tout en conservant les voix, le volume, la vitesse et les raccourcis ; l’original est conservé dans `settings-before-network.json` avant écriture. Une configuration illisible n’est pas écrasée.

Modèles : `~/Library/Caches/VoixDesDouze/models/`. Catalogue et sources vérifiées : `~/Library/Caches/VoixDesDouze/network/3.7.12.12/`. Les garder pour l’usage hors ligne. Provenance et empreintes dans `models/manifest.json` et `models/network.json` ; aucun modèle ni catalogue binaire dans Git.

Le catalogue `catalog.json` (schéma 2) conserve les **fiches PNJ complètes, indexées par identifiant**, et leur nom français : `gender` brut, `look`, dialogues/réponses, actions, animations, `moodId` et autres champs source. Les homonymes restent distincts dans les métadonnées. Les codes de genre ne sont pas encore traduits en catégories vocales ; aucune catégorie humain/gobelin/Énutrof/Écaflip n’est déduite arbitrairement. L’attribution des voix reste inchangée, par nom.

La préparation migre uniquement un ancien catalogue strictement identique aux sources vérifiées et le sauvegarde dans `catalog-before-metadata.json` ; un catalogue personnalisé différent est laissé intact. Les sources déjà présentes et vérifiées sont réutilisées sans téléchargement. Après mise à jour du logiciel, quitter puis relancer l’application depuis le Dock pour charger le nouveau schéma.

La version OCR, ses captures, ses tests et son ancien rapport sont conservés sur **`archive/ocr-before-network`**, commit `ebdcabe`, poussée sur GitHub avant le nettoyage. `master` ne contient plus ce mode.

```sh
.venv/bin/python -m pytest tests -v
.venv/bin/python scripts/prepare_network.py --verify-only
.venv/bin/python scripts/prepare_models.py --verify-only
.venv/bin/python scripts/check_audio.py --volume 0.15
```

Licences avant redistribution : [Piper GPL-3.0](https://github.com/OHF-Voice/piper1-gpl), PySide6 LGPL/GPL selon composants ; MLS/Siwis **CC-BY 4.0**, Tom **AGPLv3**, selon les MODEL_CARD conservées dans le cache. Textes et données du jeu : droits d’Ankama ; source technique communautaire [dofus-sqlite](https://github.com/ledouxm/dofus-sqlite/releases/tag/v6.0_3.7.12.12), sans approbation supposée. Aucun clonage vocal ni réemploi de ressources Romandub.

## Perspective communautaire — hors du travail actuel

Proposer ensuite le produit à Ankama : choix local des voix par PNJ, contribution du texte et de l’audio après prévisualisation/validation, enregistrements humains autorisés, stockage distant et interface web d’upvote/downvote des voix par personnage. Les meilleures propositions pourraient devenir les choix par défaut sans écraser les préférences personnelles. La lecture locale resterait autonome ; les contributions et mises à jour nécessiteraient Internet. **Serveur, uploads et votes ne sont pas implémentés.** Droits des contributeurs, autorisations relatives aux textes et modération restent à définir avant publication du service.
