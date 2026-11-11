# Voix des Douze

Application **macOS locale** : événements du client Dofus choisi → nom et texte du PNJ → voix françaises **Piper, Sopro V2 Turbo ou Pocket TTS**, mémorisées par personnage et par moteur. Une alerte sonore signale aussi une carte contenant des ressources matures avec une action de récolte disponible pour le personnage suivi, avec un panneau de debug des ressources. Aucun OCR, capture d’écran, réécriture par LLM ou API payante. Aucun proxy, modification du client ou envoi d’action au jeu.

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

### Ressources : alerte à l’arrivée et debug

Après **Démarrer l’écoute**, changer de carte pour recevoir son état complet. À la première synchronisation puis à chaque changement d’identifiant de carte, un court son de deux notes est joué si au moins une ressource est mature **et possède une action de récolte disponible pour le personnage suivi**. Revenir sur une carte peut donc sonner à nouveau ; une répétition du même état de carte, une récolte, une mise à jour des actions disponibles ou une repousse sur place ne rejoue pas l’alerte. Le son est indépendant du moteur vocal et de **Lecture automatique** ; il respecte le curseur **Volume**. **Tester le son** permet de vérifier la sortie audio sans capture.

L’encart **Ressources de la carte · debug réseau** affiche l’identifiant de carte, le nombre de ressources récoltables par le personnage suivi et les nombres de ressources matures/non matures/en récolte/inconnues. Chaque ligne indique : nom, état, **Récoltable par vous**, case Dofus, identifiant d’élément et type. Il est mis à jour par les événements de carte et d’éléments, puis vidé à l’arrêt/suspension ou à la fin de session. Il décrit les données réseau de la carte courante, pas une reconnaissance de ce qui est visible à l’écran ni des coordonnées de souris.

**Disponibilité annoncée par le serveur, pas lecture des niveaux de métier.** Les listes d’actions disponibles (champ 5) et indisponibles (champ 3) restent séparées : les deux servent à identifier une ressource, mais seule une action de récolte disponible, associée à un état mature, autorise l’alerte. Une action disponible sans rapport avec la récolte ne suffit pas. L’interface ne déduit pas la raison d’un refus (niveau, autre condition), ne garantit ni accessibilité du chemin ni réussite d’un clic et ne saisit aucun niveau manuellement. Les éléments voisins ne sont pas comptés ; un état inconnu ne déclenche pas l’alerte. La distinction personnelle reste expérimentale et doit être corroborée en jeu avec des ressources accessibles et inaccessibles au personnage.

Ce décodage est expérimental et spécifique à **3.7.13.17** : dans les champs observés, l’état 0 (omis en Protobuf) correspond à mature, 1 à non mature et 2 à récolte en cours, corroborés sur les châtaigniers et frênes. Les noms et compétences proviennent des données extraites de cette même version ; leur présence dans le catalogue ne constitue pas une validation en jeu de toutes les ressources.

Les métadonnées compactes sont livrées dans `models/harvest.json` : aucun téléchargement supplémentaire au démarrage de l’écoute. Pour les régénérer explicitement, `python scripts/build_harvest_catalog.py` vérifie les SHA256 figés de `interactivesdata.json`, `skillsdata.json` et `i18n_fr.json` ; il réutilise les fichiers de cache conformes ou télécharge ceux qui manquent. Tests ciblés : `python -m pytest tests/test_harvest.py tests/test_network.py tests/test_capture_lifecycle.py -q`.

### Repères visuels sur le jeu — POC local macOS

L’option **Repères sur Dofus** affiche une fenêtre transparente au-dessus du jeu : un anneau jaune à contour noir et un nom en blanc pour chaque ressource mature dont l’action de récolte est disponible. Les mises à jour retirent immédiatement les repères devenus indisponibles. L’overlay laisse passer les clics, ne prend pas le focus et ne s’affiche que lorsque le processus Dofus suivi est au premier plan. Arrêter ou suspendre l’écoute efface les repères ; décocher l’option les masque sans arrêter l’écoute. Aucun clic, déplacement ou code n’est envoyé au jeu.

**Calage manuel, une fois par lancement et disposition de fenêtre :**
1. Garder Dofus en **mode fenêtré**, visible sur son écran ; démarrer l’écoute et changer de carte.
2. Cliquer sur **Caler les repères…**, choisir l’écran du jeu et trois numéros de cases connues, non alignées et de préférence éloignées. Le tableau fournit les cases des ressources ; il faut pouvoir identifier leurs centres au sol dans le jeu. Ce calage nécessite une aide visuelle si le joueur ne peut pas identifier les cases.
3. Cliquer successivement sur les trois centres demandés, **au sol, pas sur le feuillage**. La fenêtre du logiciel est temporairement masquée ; ces trois clics sont interceptés par le calage et ne sont pas transmis au jeu. **Échap** annule et restaure la fenêtre. Après validation, revenir au jeu pour voir les repères.

La projection utilise la grille décalée de 14 colonnes / 560 cases, puis un calage affine sur trois centres. Elle marque les cases au sol, pas les contours des décors ; elle ne compense pas le relief. Le placement exact sur Dofus Unity reste à valider en jeu. **Déplacer, redimensionner, changer de moniteur ou zoomer la fenêtre Dofus impose de refaire le calage** : aucun suivi automatique de sa géométrie. Le plein écran natif macOS n’est pas pris en charge par ce POC. Le calage n’est pas enregistré sur disque et changer de client Dofus l’invalide. Aucune permission de capture d’écran ou d’Accessibilité n’est ajoutée.

Vérification : `python -m pytest tests/test_overlay.py tests/test_harvest.py -q`. Le parcours natif Cocoa vérifie aussi le rendu transparent, la propriété native `NSWindow.ignoresMouseEvents`, le calage et son annulation ; cela ne remplace pas la vérification de l’alignement sur une carte réelle.



### Choisir Piper, Sopro ou Pocket

Le panneau **Moteur vocal** sélectionne le moteur ; ses quatre voix apparaissent dans **Ce PNJ** et **Par défaut**. Les préférences Piper existantes sont conservées ; changer de moteur ne remplace pas les attributions des autres. La sélection est mémorisée au prochain lancement.

- **Piper** utilise le venv principal et les modèles historiques Siwis, MLS 1840, MLS 3698 et Tom.
- **Sopro V2 Turbo 2.2.0 / Pocket TTS 4.0.1 français** utilisent `.venv-neural`, séparé de PySide6/Piper. Cliquer **Télécharger / préparer** autorise explicitement l’installation isolée et le téléchargement ; sélectionner simplement un modèle absent ne lance aucune connexion. Pocket nécessite d’accepter les conditions de [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts) avec son compte Hugging Face et de configurer localement un accès autorisé. Ne jamais transmettre de jeton dans une conversation.
- Un seul processus vocal est conservé à la fois. La sélection ferme l’ancien, puis charge et préchauffe le nouveau hors du thread Qt. **Prêt** signifie que l’amorçage a réussi. **Préécouter** et les lectures sont bloquées tant que le moteur n’est pas prêt. Aucun remplacement silencieux en cas d’erreur.
- Les quatre références Sopro/Pocket sont exclusivement les **WAV synthétiques Piper** de `samples/`, contrôlés par SHA256 : aucune voix humaine ou ressource Romandub n’a été clonée. Les poids sont figés par révision, et leurs empreintes ainsi que la provenance des références sont conservées dans le cache de chaque moteur.
- L’inférence ne télécharge rien : chemins locaux uniquement, modes Hub hors ligne et sockets Python interdits dans le processus vocal. Ce garde-fou Python n’est pas un pare-feu système.
- Sopro/Pocket livrent une **phrase complète** avant le premier segment : le streaming acoustique natif n’est pas exposé ici. Une annulation tue un calcul actif ; la prochaine lecture recharge alors le modèle. Un moteur au repos reste chargé. La vitesse Sopro/Pocket utilise un rééchantillonnage : **la hauteur de voix varie également** ; à 1×, aucun rééchantillonnage.

Préparation et vérification explicites :

```sh
.venv/bin/python scripts/prepare_neural.py --engine sopro
.venv/bin/python scripts/prepare_neural.py --engine pocket
.venv/bin/python scripts/prepare_neural.py --engine sopro --verify-only
.venv/bin/python scripts/prepare_neural.py --engine pocket --verify-only
```

`--verify-only` contrôle les fichiers sans installer de dépendances ni accéder au réseau. Les fichiers personnels non conformes ne sont pas écrasés.

### Tests dédiés aux trois moteurs

`./Tester.command` exécute la suite principale, puis les tests Sopro/Pocket dans leur environnement isolé lorsqu’il est installé. Sans poids préparés, les tests réels concernés sont explicitement ignorés, pas déclarés réussis.

```sh
.venv/bin/python -m pytest tests/test_speech.py tests/test_engines.py tests/test_engine_ui.py -v
VOIX_REQUIRE_NEURAL=1 .venv-neural/bin/python -m pytest tests/test_neural_speech.py -v
.venv-neural/bin/python scripts/check_neural.py --engine sopro
.venv-neural/bin/python scripts/check_neural.py --engine pocket
```

Les tests dédiés vérifient le vrai PCM hors ligne, les références, la vitesse, les erreurs, la réutilisation des modèles, l’annulation et la fermeture des vrais processus. Le parcours Qt teste la sélection des trois moteurs et le démarrage d’afplay à volume nul. Les diagnostics produisent WAV et mesures sous `.context/neural-*`, sans écoute humaine implicite et sans les confondre avec une session Dofus.

### Raccourci dans le Dock

Après installation, exécuter `.venv/bin/python scripts/install_dock.py`. Ce script crée **`~/Applications/Voix des Douze.app`** et l’épingle au Dock, sans Terminal, sudo ou service permanent. Les autres icônes et leur ordre sont conservés ; relancer le script ne crée pas de doublon. Une sauvegarde initiale du Dock est conservée dans `~/Library/Application Support/VoixDesDouze/dock-before-shortcut.plist`.

Le lanceur utilise le venv et le code de ce dossier : ce n’est pas une distribution autonome. Garder le dossier du projet en place ; après déplacement, recréer le raccourci depuis son nouvel emplacement. Le lancement affiche l’application mais ne démarre pas l’écoute réseau.

## Contrôles et limites

- **Ce PNJ** : attribution automatique mémorisée par moteur, modifiable ; **Par défaut**, **Préécouter**, volume et vitesse 0,5×–2×. Quatre choix sont disponibles par moteur. La qualité perceptive reste à apprécier en écoutant les voix.
- Décocher **Lecture automatique** pour afficher sans parler, puis **Lire / Relire**. Suspendre coupe l’écoute et la voix ; reprendre ouvre une nouvelle session et nécessite de resynchroniser la carte.
- Raccourcis globaux **Contrôle + Option + F6/F7/F8** : suspendre/reprendre, arrêter, relire ; touches configurables, Fn parfois nécessaire. Aucune frappe de jeu n’est observée ou automatisée.
- Mode expérimental limité à **Dofus 3.7.13.17**, connexion de jeu IPv4 ou IPv6/TCP 5555 et capture Ethernet/Wi-Fi. Les adresses IPv6, y compris NAT64, sont reconnues ; les paquets IP fragmentés ne sont pas réassemblés. Le build du processus choisi est vérifié dans son journal de démarrage ; une version différente est refusée. Après une mise à jour de Dofus, les données et les messages utiles doivent être vérifiés avant de déclarer la nouvelle version compatible.
- Paramètres : le **prix de consultation bancaire (`PB`)** est remplacé par le montant décimal transmis par le jeu, sans OCR ni estimation locale. Nombre de valeurs, format numérique et marqueurs `#1`, etc. sont vérifiés. Un type inconnu, une valeur invalide ou un marqueur incohérent annule la lecture et produit un message explicite. Les autres types de paramètres restent à vérifier et à implémenter. Les choix du joueur sont détectés, mais leur texte n’est pas affiché ou lu.
- Le client choisi correspond à un processus et à son flux réseau, pas à une zone d’écran. L’isolation entre deux connexions est testée ; plusieurs comptes réels simultanés restent à valider. Le plein écran et le déplacement des panneaux n’interviennent plus dans la détection.
- Si le jeu se reconnecte ou si son flux change, l’écoute s’arrête : la relancer explicitement, puis resynchroniser la carte. Un catalogue correspondant au numéro de version ne garantit pas à lui seul que les clés obfusquées resteront stables après une mise à jour.
- Garde-fou de capture : le worker renouvelle un signal de vie local chaque seconde. Le helper arrête `tcpdump` si l’application ou le jeu disparaît, si le canal est supprimé, sur **Arrêter**, ou après **10 secondes sans renouvellement** (application bloquée). Arrêt forcé après 2 secondes si le processus résiste. Aucun service permanent ni recherche/arrêt global des autres captures. Ce garde-fou suppose le helper encore actif ; tuer brutalement le helper lui-même n’est pas couvert. Après une mise à jour, quitter puis relancer l’application pour charger le nouveau code.
- Pas encore de mesure prolongée CPU/mémoire/autonomie. Les résultats effectivement observés sont dans **`RAPPORT_TESTS.md`**.

## Données et sauvegarde OCR

Réglages : `~/Library/Application Support/VoixDesDouze/settings.json`. La migration retire les anciennes zones/options de réponses tout en conservant les voix, le volume, la vitesse et les raccourcis ; l’original est conservé dans `settings-before-network.json` avant écriture. Une configuration illisible n’est pas écrasée.

Modèles : `~/Library/Caches/VoixDesDouze/models/`. Catalogue et sources vérifiées : `~/Library/Caches/VoixDesDouze/network/3.7.13.17/`. Les garder pour l’usage hors ligne. Provenance et empreintes dans `models/manifest.json` et `models/network.json` ; aucun modèle ni catalogue binaire dans Git. Le cache de l’ancienne version est laissé intact.

Le catalogue `catalog.json` (schéma 2) conserve les **fiches PNJ complètes, indexées par identifiant**, et leur nom français : `gender` brut, `look`, dialogues/réponses, actions, animations, `moodId` et autres champs source. Les homonymes restent distincts dans les métadonnées. Les codes de genre ne sont pas encore traduits en catégories vocales ; aucune catégorie humain/gobelin/Énutrof/Écaflip n’est déduite arbitrairement. L’attribution des voix reste inchangée, par nom.

La préparation migre uniquement un ancien catalogue strictement identique aux sources vérifiées et le sauvegarde dans `catalog-before-metadata.json` ; un catalogue personnalisé différent est laissé intact. Les sources déjà présentes et vérifiées sont réutilisées sans téléchargement. Après mise à jour du logiciel, quitter puis relancer l’application depuis le Dock pour charger le nouveau schéma.

La version OCR, ses captures, ses tests et son ancien rapport sont conservés sur **`archive/ocr-before-network`**, commit `ebdcabe`, poussée sur GitHub avant le nettoyage. `master` ne contient plus ce mode.

```sh
.venv/bin/python -m pytest tests -v
.venv/bin/python scripts/prepare_network.py --verify-only
.venv/bin/python scripts/prepare_models.py --verify-only
.venv/bin/python scripts/check_audio.py --volume 0.15
```

Licences avant redistribution : [Piper GPL-3.0](https://github.com/OHF-Voice/piper1-gpl), PySide6 LGPL/GPL selon composants ; MLS/Siwis **CC-BY 4.0**, Tom **AGPLv3**, selon les MODEL_CARD conservées dans le cache. [Sopro Apache-2.0](https://github.com/samuel-vitorino/sopro), [Pocket et conditions des poids](https://huggingface.co/kyutai/pocket-tts) ; les références synthétiques ne suppriment pas les obligations des modèles Piper sources. Textes et données du jeu : droits d’Ankama ; source technique communautaire [dofus-sqlite](https://github.com/ledouxm/dofus-sqlite/releases/tag/v6.0_3.7.13.17), sans approbation supposée. Aucun clonage d’enregistrement humain ni réemploi de ressources Romandub.

## Perspective communautaire — hors du travail actuel

Proposer ensuite le produit à Ankama : choix local des voix par PNJ, contribution du texte et de l’audio après prévisualisation/validation, enregistrements humains autorisés, stockage distant et interface web d’upvote/downvote des voix par personnage. Les meilleures propositions pourraient devenir les choix par défaut sans écraser les préférences personnelles. La lecture locale resterait autonome ; les contributions et mises à jour nécessiteraient Internet. **Serveur, uploads et votes ne sont pas implémentés.** Droits des contributeurs, autorisations relatives aux textes et modération restent à définir avant publication du service.
