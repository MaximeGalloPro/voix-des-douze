# Tests et limites réellement observés

## Environnement et périmètre

Apple M4, 32 Go, macOS 26.5.2 arm64 ; Python 3.13.8 dans `.venv`, PySide6 6.10.1, Piper 1.8.0, ONNX Runtime 1.31.1 CPU. Versions figées dans `requirements.lock`.

La branche `archive/ocr-before-network`, commit `ebdcabe`, a été poussée avant suppression du mode OCR. Elle conserve ses images, tests et mesures historiques. Le présent rapport décrit la bascule vers le réseau uniquement ; les mesures OCR ne sont plus des résultats du produit courant.

## Vérifications après retrait de l’OCR

```sh
.venv/bin/python -m pytest tests -q
.venv/bin/python scripts/prepare_network.py --verify-only
./Lancer.command --help
```

Résultats : **24 passed in 15.98s** ; catalogue et sources vérifiés pour Dofus **3.7.12.12** ; aide du lanceur limitée à `-h/--help`, sans anciens modes image ou démonstration.

Les tests couvrent :

- ouverture, réplique, choix du joueur, fermeture et réouverture identique ; annulation de l’ancien texte avant les transitions ; dédoublonnage d’une réplique inchangée ;
- identité d’entité limitée à la carte observée : refus d’un PNJ non synchronisé ou d’un identifiant repris sur une autre carte ;
- répliques inconnues ou à paramètres : lecture invalidée et erreur explicite, aucun texte de substitution ; messages étrangers, dont le chat, non interprétés par la conversation ;
- fragmentation PCAP/TCP, chevauchements, retransmissions, réordonnancement, retour des numéros de séquence sur 32 bits et refus d’une trame incompatible ;
- isolation de deux connexions : les paquets d’un autre port client ne modifient pas le dialogue affiché ;
- intégration déterministe événements Protobuf construits pour le test → dialogue → voix persistante → vrai Piper → PCM non silencieux et durée cohérente, connexions Python interdites pendant ce parcours ; **ce test synthétique n’est pas une nouvelle session de jeu** ;
- voix réelles, modèles manquants, vitesses invalides, persistance normalisée des attributions, fichier de réglages corrompu conservé ;
- migration des anciens réglages : suppression des zones/options retirées, conservation des voix et réglages audio, sauvegarde originale non réécrite au lancement suivant ;
- annulation concurrente pendant génération et lecture, avec les tests existants de Playback.

## Session Dofus réelle : réseau et lecture vocale

Une capture passive ponctuelle, autorisée explicitement par l’utilisateur puis authentifiée via macOS, a porté sur le seul flux TCP du processus Dofus sélectionné. **149 paquets / 18 371 octets** ont permis de réassembler 4 981 octets serveur et 1 318 octets client, sans lacune ni fin de flux non interprétée dans ce relevé.

Sur cette capture : une ouverture du dialogue d’**Agride Shakoku**, cinq répliques successives et cinq choix du joueur ont été identifiés. L’entité de carte a été reliée au PNJ 59 ; le nom et les cinq textes français ont été résolus avec les données publiques correspondant au numéro de version. Cette première preuve a été faite après capture, pas en direct.

Deux parcours en direct ont ensuite été exécutés :

1. Exercice borné réseau → première réplique → Piper/Siwis → vrai afplay : lancement audio environ **1,28 s** après détection de l’ouverture. L’utilisateur a répondu **« Oui, je l’entends »**. Choix suivant détecté et lecture invalidée. Capture arrêtée ensuite.
2. Mode intégré à la fenêtre PySide6, avant retrait des contrôles OCR : vrai bouton de démarrage, autorisation macOS, indicateur du PID/version/interface, changement de carte, retour au PNJ, nom et texte affichés, première réplique et réplique suivante lues. L’utilisateur a confirmé **« Tout fonctionne »**, à une question portant aussi sur l’interruption lorsqu’il répond. Délais soumission TTS → lancement d’afplay : **1 073 ms**, puis **925 ms**. Clic sur Arrêter, arrêt du worker/capture et du processus audio vérifiés. Aucun événement d’image reçu par l’interface pendant ce mode.

Ces délais mesurent le lancement du processus audio, pas le premier son physique au haut-parleur. Le retour de l’utilisateur confirme une audition, pas une mesure acoustique ou une évaluation exhaustive de la prononciation.

## Interface simplifiée : vérification après nettoyage

Une vraie fenêtre PySide6/Cocoa de **960 × 720** a été ouverte. La capture réelle précédente a été rejouée hors ligne, par fragments de 31 octets, à travers le décodeur et la conversation du produit, puis vers cette interface et le moteur Piper existant.

Observé : Agride Shakoku et sa première réplique affichés, vrai afplay démarré (**1 295 ms** après soumission), bouton Arrêter cliqué via QTest, processus audio terminé et dialogue effacé. Le démarrage sans client sélectionné a également produit l’erreur attendue sans lancer de capture. Aucun contrôle OCR, zone ou image dans la nouvelle fenêtre.

**Ce rejeu n’est pas un nouveau test réseau en direct après le nettoyage.** Le contrôleur réseau utilisé dans le parcours live précédent est conservé ; l’interface et le modèle de dialogue ont été simplifiés. Réglages temporaires du parcours retirés ; préférences personnelles préservées. Résultats et capture conservés localement dans `.context/network-only-ui-results.json` et `.context/network-only-ui.png`, hors Git.

## Préparation, permissions et données

`scripts/prepare_network.py` a réellement téléchargé les trois sources figées, vérifié taille et SHA256 et préparé **6 472 noms de PNJ / 55 127 répliques françaises**. Les sources et leur catalogue restent dans le cache utilisateur. Aucun téléchargement n’est effectué implicitement par les tests ou par le démarrage de l’écoute.

L’accès BPF est refusé avec les droits utilisateur ordinaires sur cette machine. Seul le helper de capture reçoit une autorisation administrateur par session ; le lecteur vocal et l’interface restent non privilégiés. La capture passe par un FIFO privé et n’écrit pas de fichier de paquets. Arrêt explicite, arrêt audio et fin de la capture ont été observés dans le parcours intégré. Aucun service permanent n’a été installé, aucune permission BPF durable modifiée, aucun proxy ni envoi d’action au jeu ajouté.

## Non validé / limites connues

- autres PNJ en jeu réel, plusieurs comptes simultanés, reconnexions réelles et longues sessions CPU/mémoire/autonomie ; les tests de transitions et d’isolation ne remplacent pas ces essais ;
- dialogues paramétrés : explicitement refusés pour éviter de lire des noms/valeurs non résolus ;
- texte des choix du joueur : non affiché/non lu, seul l’événement de réponse est utilisé pour annuler ;
- version différente de 3.7.12.12, IPv6 et interfaces de capture autres qu’Ethernet/Wi-Fi : non pris en charge ;
- lecture démarrée au milieu d’une carte : un changement de carte est nécessaire pour observer les identités, aucun ancien état de session n’est réutilisé ;
- frappe physique des raccourcis globaux pendant le jeu non vérifiée ; enregistrement Carbon exercé sans surveillance des frappes ;
- compatibilité contractuelle et droits de redistribution à clarifier avec Ankama avant diffusion. Une capture passive n’est pas une autorisation de l’éditeur.

Les WAV et rapports vocaux de `samples/` restent des échantillons de synthèse locale. Aucun serveur communautaire, vote, upload, clonage vocal ou contact avec Ankama/Romandub n’a été mis en place.

## Ajout des métadonnées PNJ

Le catalogue a été migré vers le schéma 2 sur les sources réelles déjà présentes, avec connexions Python interdites pendant tout le smoke : **6 472 fiches complètes / 6 472 noms / 55 127 répliques**. Chaque champ de chaque fiche conservée a été comparé à sa source. Codes `gender` observés : 0 pour 4 510 fiches, 1 pour 1 587, 2 pour 375 ; aucune signification féminin/masculin n’a été supposée. Exemple : Agride Shakoku, identifiant 59, `gender: 0`, `look: "{6191}"`.

L’ancien catalogue a été sauvegardé avant remplacement atomique. Le rejeu hors ligne de la capture Dofus antérieure identifie toujours Agride et sa première réplique. Le fichier des réglages/attributions de voix est identique avant et après cette opération. Catalogue et sources vérifiés ensuite avec `--verify-only`. Aucun nouvel essai en jeu ni changement automatique de voix fondé sur ces métadonnées.

Suite exécutée : **25 tests réussis, 1 échec, en 5,72 s**. Les deux nouveaux tests vérifient la migration avec sauvegarde, les homonymes par identifiant, les PNJ sans traduction, les codes inconnus conservés, la préparation répétée sans modification et le refus d’écraser un catalogue personnalisé. L’échec concerne `test_real_speed_changes_audio_duration` : 4,5395 s à vitesse 0,5 contre 1,8692 s à vitesse 2, sous le rapport minimal de 2,5 attendu par ce test. Le moteur de synthèse et ce test n’ont pas été modifiés ; l’échec est signalé, pas masqué par un ajustement du seuil.

## Arrêt de capture en cas de disparition de session

Un signal de vie renouvelé chaque seconde complète les contrôles existants du PID et du fichier d’arrêt. Sans renouvellement pendant 10 secondes, le helper termine sa propre capture ; si elle résiste, il la tue après 2 secondes. Cela couvre notamment un worker bloqué alors que le processus existe encore ou un PID réutilisé. Aucun arrêt global de `tcpdump`, service permanent ou élévation supplémentaire.

Vérification : `.venv/bin/python -m pytest tests/test_capture_lifecycle.py tests/test_network.py -q` : **12 tests réussis en 9,53 s**. Les cinq nouveaux cas utilisent de vrais sous-processus : propriétaire tué brutalement, arrêt explicite, canal supprimé, propriétaire vivant sans renouvellement avec capture ignorant SIGTERM, et renouvellement maintenant la capture active jusqu’à l’arrêt.

Smoke hors tests avec le binaire système `/usr/sbin/tcpdump`, en lecture hors ligne de l’entrée standard bloquée, sans accès réseau ni privilèges : expiration du délai de production de 10 secondes, sortie **SIGTERM (-15) après 10,120 s**, propriétaire toujours vivant. Ce n’est pas un essai de capture privilégiée dans Dofus. Le helper lui-même doit rester actif pour superviser son enfant ; son propre SIGKILL n’est pas couvert.

Diagnostic de la connexion utilisateur : Dofus actif avec une connexion TCP 5555 IPv6, aucun `tcpdump` trouvé parmi les processus actifs. Le refus de cette connexion provient de la limite IPv4 actuelle, pas d’une capture orpheline. Le support IPv6 n’a pas été ajouté dans ce correctif.
