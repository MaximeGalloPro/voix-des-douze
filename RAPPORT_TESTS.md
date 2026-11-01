# Validation de Voix des Douze — 1er novembre 2026

## Environnement et périmètre

Mac réellement accessible : Apple M4, 32 Go, macOS 26.5.2 (arm64), Python 3.13.8 dans `.venv`, PySide6 6.10.1, Piper 1.8.0, ONNX Runtime 1.31.1 CPU (2 threads intra / 1 inter), Swift 6.3.3. Versions transitives dans `requirements.lock`. Aucune installation globale ni sudo.

Les trois captures fournies représentent **Kerubim Crépin**, **Nacf Virgil**, **Al Etsop**. Elles ne représentent pas Herdegrize, cité dans le prompt initial. Des recadrages réels conservant le profil ICC retirent le chat et les noms de compte pour les fixtures Git ; ils sont replacés sur un fond neutre aux coordonnées originales pour les tests. Les trois originaux ont également été traités lors du développement. Le parcours graphique final utilise l’original d’Al Etsop.

**Aucune session Dofus en direct n’a été capturée ou testée : la permission écran est refusée. Aucune écoute humaine ni capture audio de sortie matérielle n’a été réalisée.**

## Résultats automatisés

Commande complète finale, avec interdiction réseau appliquée aux processus par macOS :

```sh
/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' .venv/bin/python -m pytest tests -q
```

Résultat : **31 passed in 17.12s**. Une tentative de connexion TCP sous la même sandbox a produit `PermissionError: [Errno 1] Operation not permitted`, vérifiant que le refus réseau est effectif. La suite normale avait également produit 31 succès. Aucun moteur OCR ou TTS réel n’a été remplacé par une doublure pour les tests d’intégration.

| Domaine | Preuve réelle / limites |
|---|---|
| OCR Apple Vision français | Égalité avec transcriptions manuelles des 3 noms, dialogues complets, ordre des lignes et réponses séparées ; seules apostrophes et espaces typographiques normalisés. Le chiffre **0 Kamas** doit rester un chiffre. |
| Régions manuelles | Une sélection vide ou noire ne doit pas reconnaître du texte ailleurs dans l’image. Rectangles hors limites rejetés. Sélection visuelle native exercée en complément. |
| Synthèse Piper | Quatre choix réels : Siwis 0, MLS 1840 → index 0, MLS 3698 → index 1, Tom 0. Configurations françaises, identifiants et licences vérifiés. PCM16 mono, 22050 Hz (Siwis/MLS), 44100 Hz (Tom), durées cohérentes, signal non silencieux, sorties distinctes. |
| Intégration | Image réelle d’Al Etsop → Vision réel → attribution persistante → Piper réel → WAV relu et validé, sans réseau autorisé. |
| Dédoublonnage | Réplique inchangée non relue, ponctuation tolérée, variations OCR conservatrices, nombres/réponses courtes/négation non fusionnés ; changement de PNJ et fermeture/réouverture autorisent la lecture. |
| Persistance | Attributions conservées après réouverture du stockage, accents/casse normalisés ; voix de secours sans nom ; configuration corrompue laissée intacte. Modèle existant non conforme laissé intact lors de la préparation. |
| Annulation | Test déterministe : génération ancienne retenue par une barrière, annulation, nouveau travail, libération de l’ancien ; seul le nouvel audio peut atteindre le lecteur. Test séparé d’arrêt pendant lecture. Doublures autorisées ici pour contrôler les courses ; lecteur réel exercé dans le parcours natif. |
| Erreurs | Permission refusée : vrais appels natifs de liste et capture → erreur explicite, sans demande silencieuse. Identifiant de fenêtre invalide : `WindowClosedError`. Texte blanc : aucun segment. Modèle absent / vitesse invalide : erreurs utiles. Une vraie fermeture de fenêtre capturée reste non validée faute de permission. |

## Interface native effectivement exercée

Une vraie fenêtre PySide6/Cocoa a été ouverte et pilotée avec la boucle d’événements Qt, sans navigateur et sans modifier les réglages de l’utilisateur. Réglages temporaires retirés après vérification. Résultats conservés dans `samples/ui-results.json` ; captures de l’interface et du sélecteur de zones dans `.context/`.

Vérifications effectuées :

- Reconnaissance de l’original d’Al Etsop, texte complet et deux réponses ; première lecture automatique par le vrai lecteur `afplay`.
- Tracé de rectangle à la souris dans le vrai sélecteur ; conversion des coordonnées d’aperçu vers les coordonnées normalisées vérifiée.
- Sauvegarde des régions manuelles puis retour au repérage automatique.
- Option de lecture des réponses et maintien d’une voix distincte de celle du PNJ.
- Démonstration sur images **synthétiques**, mais avec OCR et TTS réels ; passage Gardienne → Voyageur.
- Suspendre coupe capture et audio ; reprendre redémarre la surveillance ; arrêter interrompt le lecteur ; lecture à la demande et relecture exercées.
- Attribution persistante vérifiée après réouverture du fichier de réglages.
- Trois raccourcis Carbon globaux enregistrés sans erreur. **La frappe physique des raccourcis dans Dofus n’a pas été testée.**
- Bouton **Captures de test** : menu natif ouvert et chacune des trois entrées cliquée avec QTest ; noms, dialogues complets et réponses comparés aux transcriptions manuelles des tests OCR. Aperçu sur fond neutre 2048 × 1280, état distinct `test:…` pour les zones manuelles ; fenêtre de 1200 px sans débordement du nouveau bouton. Relecture d’Al Etsop jusqu’au lancement réel d’afplay, puis arrêt vérifié ; aucune écoute humaine. Capture du parcours : `.context/captures-test-ui.png`.

Les workers de surveillance et de synthèse déclarent une activité utilisateur macOS pendant leur travail pour ne pas être traités comme une tâche de fond facultative ; la veille système reste autorisée. Le moteur Swift fait de même pour ses requêtes. L’aperçu n’est régénéré que si les panneaux changent.

Le premier pilote de QA utilisait des attentes `QTest.qWait` successives, perturbant l’ordonnancement des workers et produisant des temps de 2,5–5,8 s et une observation d’arrêt prématurée. Il a été remplacé par de vraies boucles `QEventLoop`. Les mesures de référence ci-dessous viennent de ce dernier parcours, pas du pilote initial. L’arrêt utilise aussi SIGKILL pour ne pas dépendre de la coopération d’afplay ; aucune garantie de latence acoustique matérielle n’en est déduite.

## Lecture complète : preuve technique, pas sensorielle

Commande effectivement exécutée :

```sh
.venv/bin/python scripts/check_audio.py --volume 0.15
```

| WAV réel | Durée WAV | Temps du processus afplay | Résultat |
|---|---:|---:|---|
| MLS 1840 | 7,117 s | 7,935 s | code 0, pas de stderr |
| MLS 3698 | 9,671 s | 10,477 s | code 0, pas de stderr |
| Siwis | 3,820 s | 4,138 s | code 0, pas de stderr |
| Tom | 4,243 s | 4,643 s | code 0, pas de stderr |

Chaque WAV non silencieux a été donné entièrement au lecteur, qui s’est terminé normalement après une durée cohérente. Résultats bruts : `samples/audio-completion.json`. Cela **ne prouve pas** que le son sortait du bon périphérique, qu’il était audible, que la prononciation était bonne ou que les voix étaient agréables. Le choix perceptif se fait avec **Préécouter la voix** dans l’application. Aucun classement de qualité humaine n’est inventé.

## Performances observées — pas de généralisation

Mesures sur ce seul M4, à partir d’images fixes ; aucune charge de Dofus en direct ni autre machine représentative du parc des joueurs.

### Parcours graphique final

Après préchauffage Piper et Vision :

- Al Etsop : **288 ms d’OCR** visibles dans l’interface ; **315 ms** de la soumission TTS au lancement d’afplay ; **820 ms après la stabilité visuelle** jusqu’au lancement du processus audio.
- Démonstration synthétique : **1,018 s** après stabilité jusqu’au lancement audio.
- Relecture manuelle : **925 ms** de la soumission au lancement du processus.
- Arrêt : sortie du processus observée en **128 ms**, incluant le contrôle dans la boucle Qt. Ce n’est pas une mesure de la fin du son au haut-parleur.

L’objectif de moins de deux secondes après stabilisation est atteint sur ces scénarios fixes préchauffés, **pas encore validé en jeu réel**, et n’est pas une promesse sur toutes les répliques. Le timestamp est celui du lancement d’afplay, pas de l’instant acoustique exact. La détection automatique vise le thème des captures fournies ; les autres rendus nécessitent des zones manuelles ou une validation supplémentaire.

### Synthèse isolée et pipeline reproductible

`samples/speech-results.json` : pour la même courte phrase, premier PCM **2,689–6,154 s à froid**, puis **0,192–0,607 s à chaud** sur les quatre choix. « Froid » signifie nouvelle session ONNX, pas cache disque macOS vidé. Le préchauffage de tous les modèles est donc explicite, asynchrone, sans son ; il ne doit pas être confondu avec la latence d’une lecture à chaud.

`samples/pipeline-results.json` contient quatre passages par capture réelle, temps de détection, OCR, premier PCM, synthèse totale et lancement réel d’afplay. Préchauffage des trois modèles : **10,995 s**. Le premier appel Vision est identifié. Le benchmark direct exclut la cadence de capture et l’attente de stabilité de 350 ms ; ses résultats ne remplacent pas le parcours graphique.

| Capture / voix automatiquement attribuée | OCR sur 4 passages | Image → lancement afplay |
|---|---:|---:|
| Kerubim / Siwis | 0,574–1,096 s | 1,092–1,594 s |
| Nacf / MLS 1840 | 0,432–0,586 s | 1,072–1,384 s |
| Al Etsop / MLS 3698 | 0,481–0,573 s | 1,391–1,791 s |

Le maximum OCR de Kerubim inclut le premier appel Vision du processus. Piper introduit des variations de génération ; les sorties ne sont pas attendues identiques bit à bit. Ces fourchettes décrivent ce passage de mesure, pas une garantie sur les autres dialogues. Reproduction :

```sh
.venv/bin/python scripts/prepare_models.py --verify-only --samples-dir samples
.venv/bin/python scripts/benchmark.py --play
```

Une tentative de regrouper les trois zones en une seule image OCR a accéléré le traitement, mais dégradait **0 Kamas → O Kamas**. Elle a été retirée ; aucun correctif spécial sur le mot Kamas ni assouplissement de l’attente du test n’a été conservé.

## Installation, données et validations restantes

`Installer.command` a été exécuté à nouveau : toutes les versions déjà satisfaites, neuf fichiers de modèles/configurations/notices vérifiés, moteur Apple compilé, permission écran `False`. `Lancer.command --help` a été exécuté. Les lancements suivants n’installent ni ne téléchargent rien. La première compilation du helper Swift a pris environ 39 s dans le travail initial ; le cache évite de la répéter à code identique.

Les versions ont été choisies après consultation des documentations [Piper](https://github.com/OHF-Voice/piper1-gpl), [PySide6](https://doc.qt.io/qtforpython-6/gettingstarted.html), Apple ScreenCaptureKit/Vision et des [activités macOS](https://developer.apple.com/documentation/foundation/processinfo/activityoptions/userinitiatedallowingidlesystemsleep). Le manifeste fige la révision Hugging Face et les SHA256. MLS/Siwis : CC-BY 4.0 ; Tom : AGPLv3, d’après les MODEL_CARD téléchargées et vérifiées. Plusieurs signaux distincts ne prouvent pas plusieurs bonnes interprétations vocales.

Actions manuelles indispensables pour la validation restante :

1. Lancer `Lancer.command`, cliquer **Autorisations…**, accorder l’enregistrement d’écran dans les réglages macOS à l’entrée présentée par le système, relancer puis actualiser.
2. Choisir Dofus et vérifier un dialogue réel, puis plusieurs comptes sans fuite d’une fenêtre vers l’autre, un changement de panneau, une fermeture/réouverture, une fenêtre fermée, les autres Spaces/plein écran/arrière-plan et un redimensionnement Retina. La géométrie et l’isolation sont implémentées via ScreenCaptureKit ; elles ne sont pas présentées comme validées visuellement en jeu.
3. Écouter les préécoutes, vérifier le périphérique de sortie et choisir les voix agréables. Aucun accès sensoriel à cette audition n’est prétendu.

Le dépôt public `https://github.com/MaximeGalloPro/voix-des-douze` a été créé et relié comme remote `origin`, sans push. Aucun contact avec Romandub, aucune affiliation, aucun clonage vocal ou réemploi de leurs ressources.
