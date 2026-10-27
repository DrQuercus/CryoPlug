"""Fiches d'aide (en français) pour chaque type de job.

Chaque fiche contient :
  purpose  à quoi sert le job (une ou deux phrases)
  when     cas où il est pertinent
  avoid    cas où il ne l'est pas / pièges
  inputs   quelles entrées utiliser
  tips     conseils de paramétrage et d'interprétation
  next     jobs qui suivent naturellement (noms de types de jobs)

Les fiches sont affichées dans le constructeur de jobs, dans le panneau de détails, dans la page
d'aide de l'interface et dans docs/JOBS.md (généré par ``cryoplug docs-jobs``).
"""
from __future__ import annotations

from typing import Any

HELP: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------ Import
    "import_cryosparc": {
        "purpose": "Point d'entrée habituel : récupère la reconstruction finale d'un job CryoSPARC (raffinement homogène, "
                   "non-uniforme ou local, Sharpening Tools, Local Filtering) et recalcule la FSC gold-standard.",
        "when": [
            "Votre meilleure carte vient de CryoSPARC.",
            "Vous avez besoin des demi-cartes non filtrées (LocScale, spIsoNet, Servalcat, FSC, dépôt EMDB).",
            "Vous voulez que la résolution soit ensuite remplie automatiquement (« auto ») dans tous les jobs.",
        ],
        "avoid": [
            "Carte produite par un autre logiciel (RELION, cisTEM, EMDB…) : utilisez « Import maps ».",
            "Job de classification 3D ou carte intermédiaire : importez le raffinement final.",
        ],
        "inputs": "Le dossier du job CryoSPARC (ex. /data/CS-projet/J245). La dernière itération est choisie automatiquement : "
                  "vérifiez la liste dans le tableau « Imported files ».",
        "tips": [
            "Laissez « Compute half-map FSC » activé : la résolution corrigée est propagée aux jobs suivants.",
            "Indiquez la symétrie réellement imposée (C1, C2, D7…) : LocScale, EMmerNet et le dépôt l'utilisent.",
            "« copy » rend le projet autonome ; « symlink » économise de l'espace mais dépend du dossier CryoSPARC.",
        ],
        "next": ["directional_fsc", "locscale", "modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "import_sequence",
                 "draw_mask", "relion_local_refine"],
    },
    "import_maps": {
        "purpose": "Importe des demi-cartes, une carte complète, une carte affûtée et un masque venant de n'importe quel logiciel.",
        "when": [
            "Reconstruction RELION, cisTEM, cryoDRGN, ou carte téléchargée de l'EMDB.",
            "La taille de pixel de l'en-tête est fausse (calibration du grossissement) : option de correction.",
            "Valider ou déposer un modèle existant (workflow « Validate & prepare deposition »).",
        ],
        "avoid": ["Job CryoSPARC : « Import from CryoSPARC » trouve les fichiers tout seul."],
        "inputs": "Les deux demi-cartes non filtrées sont le plus utile ; la carte affûtée sert à la construction et au dépôt.",
        "tips": [
            "Sans demi-cartes, pas de FSC, ni LocScale/spIsoNet/Servalcat optimaux.",
            "Une correction de taille de pixel s'applique aux copies importées (mode copy obligatoire).",
        ],
        "next": ["map_fsc", "directional_fsc", "locscale", "import_model"],
    },
    "import_model": {
        "purpose": "Importe un modèle atomique : fichier PDB/mmCIF, entrée de la PDB ou prédiction de l'AlphaFold Database.",
        "when": [
            "Structure homologue ou ancienne structure à recaler dans la nouvelle carte.",
            "Prédiction AlphaFold pour un docking (résolution trop basse pour une construction de novo).",
            "Modèle final à valider ou à déposer.",
        ],
        "avoid": [
            "Affiner directement un modèle AlphaFold : passez d'abord par « Process predicted model » puis un ajustement dans la carte.",
        ],
        "inputs": "Fichier PDB/mmCIF, code PDB (4 caractères) ou accession UniProt (AlphaFold DB).",
        "tips": [
            "Une colonne B-factor de type pLDDT est détectée et signalée.",
            "La sortie « sequence » est extraite du modèle : pratique, mais ne remplace pas la séquence de l'échantillon pour la vérification avant dépôt.",
        ],
        "next": ["phenix_process_predicted_model", "fit_in_map", "chimerax_fitmap", "phenix_dock_in_map", "mapmodel_validation"],
    },
    "import_sequence": {
        "purpose": "Déclare la ou les séquences de l'échantillon (FASTA collé, fichier ou accessions UniProt). "
                   "Les enregistrements protéine, ARN et ADN sont séparés automatiquement.",
        "when": [
            "Dès que la composition est connue : elle guide ModelAngelo, CryoAtom2, Boltz-2 et ColabFold.",
            "Pour la vérification séquence/modèle de la checklist avant dépôt.",
        ],
        "avoid": ["Répéter une séquence pour chaque copie d'un homo-oligomère : une seule entrée par entité distincte."],
        "inputs": "Un enregistrement FASTA par entité (chaîne différente).",
        "tips": [
            "Incluez les étiquettes (tags) réellement présentes si elles sont visibles dans la carte.",
            "Les ARN/ADN sont reconnus à leur composition (A, C, G, U/T).",
        ],
        "next": ["modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "boltz_predict", "colabfold_predict"],
    },
    "import_particles": {
        "purpose": "Importe des particules pour l'analyse d'hétérogénéité (cryoDRGN) : un job CryoSPARC ou un fichier .cs "
                   "(fusionné avec son fichier « passthrough »), ou un fichier .star de RELION. Seules les métadonnées sont "
                   "lues : les images restent où elles sont.",
        "when": [
            "Les particules ne viennent pas d'un job déjà importé avec « Import from CryoSPARC ».",
            "Particules exportées d'un autre projet, ou d'un autre logiciel (.star).",
        ],
        "avoid": ["Particules sans poses (extraction, classification 2D) pour un entraînement cryoDRGN classique : il lui "
                  "faut un raffinement consensus. cryoDRGN-AI (ab initio), lui, les accepte."],
        "inputs": "Le dossier du job de raffinement consensus (ses particules portent les poses), un .cs ou un .star ; pour "
                  "cryoDRGN-AI, n'importe quel job qui contient les particules (Select 2D, extraction…).",
        "tips": [
            "« Import from CryoSPARC » importe déjà les particules du job en même temps que les cartes.",
            "Si les chemins des images sont cassés, indiquez le dossier des images (Image folder).",
        ],
        "next": ["cryodrgn_backproject", "cryodrgn_train", "cryodrgn_abinit", "relion_local_refine", "relion_reconstruct",
                 "relion_class2d"],
    },
    "import_volume_series": {
        "purpose": "Importe des séries de volumes : 3D Variability Display et 3D Flex Generate de CryoSPARC, cryoDRGN, "
                   "multi-body de RELION… depuis un dossier, un ZIP ou un motif de fichiers. Une série par composante.",
        "when": [
            "Vous avez lancé la 3D Variability (3DVA) ou 3D Flex dans CryoSPARC et voulez interpréter les mouvements ici.",
            "Comparer des volumes de différents états produits ailleurs.",
        ],
        "avoid": ["Volumes de boîtes ou de tailles de pixel différentes dans une même série."],
        "inputs": "Le dossier du job 3D Variability Display (ou le ZIP téléchargé d'une composante).",
        "tips": [
            "Les fichiers sont regroupés par « component N » dans leur nom, puis triés par numéro de frame.",
            "Les coordonnées de chaque particule le long des composantes s'importent avec le job 3D Variability "
            "(Import from CryoSPARC) : elles donnent un espace latent.",
            "Bouton 3D : la série se joue comme un film dans le visualiseur.",
        ],
        "next": ["series_analysis", "extract_volume"],
    },
    # ---------------------------------------------------------- Reconstruction
    "relion_local_refine": {
        "purpose": "Raffinement 3D gold-standard dans RELION avec recherches angulaires locales autour des poses des "
                   "particules (raffinement consensus CryoSPARC ou job RELION précédent), focalisé par un masque : "
                   "l'équivalent RELION du « Local Refinement » de CryoSPARC, avec la régularisation Blush.",
        "when": [
            "Une région (domaine, sous-complexe, ligand) est floue dans la carte consensus : raffinement local avec son masque.",
            "Après une soustraction de signal (« RELION signal subtraction ») : raffinement focalisé des particules "
            "soustraites, recentrées et recadrées.",
            "Comparer un raffinement local RELION (Blush) à celui de CryoSPARC sur les mêmes particules.",
            "Obtenir le modèle RELION (optimiser) nécessaire à la soustraction : une courte passe sur tout le complexe.",
        ],
        "avoid": [
            "Région trop petite (< 100-150 kDa sans signal soustrait) : l'alignement local devient instable ; soustrayez "
            "d'abord le reste du complexe et essayez Blush.",
            "Particules sans poses (sortie de classification 2D) : il faut un raffinement consensus.",
            "Masque serré au bord de la densité : prenez un masque doux (dilatation + bord ≥ 6 Å), sinon la FSC est surestimée.",
        ],
        "inputs": "Particules avec poses (import CryoSPARC du raffinement consensus, sélection, particules soustraites), "
                  "la carte consensus non affûtée comme référence et le masque de la région (dessiné sur la carte consensus).",
        "tips": [
            "Les demi-jeux CryoSPARC (alignments3D/split) sont conservés : la FSC reste gold-standard.",
            "Échantillonnage 1,8° avec recherches locales dès le départ (± 11°) : le réglage standard d'un raffinement local.",
            "Blush (RELION 5) améliore nettement les petites régions flexibles ; il demande le modèle Blush installé.",
            "Toutes les cartes sortent sur la grille de la carte consensus, même après recentrage : elles se superposent "
            "au modèle et servent directement à la carte composite.",
            "MPI : au moins 3 processus (un maître + un par demi-jeu) ; avec des GPU, 2 processus par GPU + 1.",
        ],
        "next": ["relion_subtract", "composite_map", "locscale", "modelangelo_build", "local_resolution"],
    },
    "relion_subtract": {
        "purpose": "Soustraction de signal (relion_particle_subtract) : retire de chaque image la projection de tout ce qui "
                   "est hors du masque, recentre les particules sur la région gardée et les recadre dans une boîte plus "
                   "petite. Option : expansion de symétrie préalable (chaque copie d'un complexe symétrique devient une particule).",
        "when": [
            "Raffinement ou classification focalisés d'une région que le reste du complexe empêche d'aligner.",
            "Complexe symétrique dont une copie varie : expansion de symétrie + soustraction, puis classification en C1.",
            "Préparer une analyse cryoDRGN d'une seule région (petite boîte = entraînement plus rapide).",
        ],
        "avoid": [
            "Particules qui n'ont pas été raffinées dans RELION : la soustraction utilise le modèle RELION du raffinement "
            "(lancez d'abord « RELION local refinement » sur tout le complexe, recherches locales, quelques heures).",
            "Masque trop serré : le signal résiduel de la région serait soustrait ; dilatez-le de quelques Å.",
        ],
        "inputs": "Les particules de « RELION local refinement » (tout le complexe) et le masque doux de la région à garder.",
        "tips": [
            "Boîte automatique : 1,5 × l'étendue du masque (place pour le signal délocalisé par la CTF).",
            "Le recentrage se fait par voxels entiers : les cartes reviennent exactement sur la grille consensus.",
            "Les sorties « map » et « mask » reprennent la carte du raffinement et le masque : le job suivant se remplit tout seul.",
            "Expansion de symétrie : × n particules (C3 → 3 copies), même demi-jeu pour les copies d'une particule.",
        ],
        "next": ["relion_local_refine", "relion_class3d", "cryodrgn_train"],
    },
    "relion_class3d": {
        "purpose": "Classification 3D RELION de particules avec poses, focalisée par un masque, sans alignement (ou avec "
                   "recherches locales) : sépare les états d'une région, typiquement sur des particules soustraites.",
        "when": [
            "Hétérogénéité discrète d'un domaine (ligand lié / libre, conformation ouverte / fermée).",
            "Après expansion de symétrie + soustraction : trouver les copies dans un état donné.",
            "Nettoyer un jeu de particules avant un raffinement local.",
        ],
        "avoid": [
            "Hétérogénéité continue : cryoDRGN ou la 3D variability de CryoSPARC la décrivent mieux.",
            "Particules sans poses : il faut un raffinement consensus d'abord.",
        ],
        "inputs": "Particules (soustraites de préférence), la carte de référence (consensus ou raffinement focalisé) et le "
                  "masque de la région.",
        "tips": [
            "T = 4 par défaut ; 10 à 40 pour de petites régions soustraites (plus de détails, plus de bruit).",
            "Sans alignement (--skip_align), 25 itérations suffisent généralement ; regardez la stabilité des classes.",
            "Chaque classe donne une carte (sur la grille consensus) et ses particules : enchaînez par un raffinement local.",
        ],
        "next": ["relion_local_refine", "relion_reconstruct", "cryodrgn_train"],
    },
    "relion_postprocess": {
        "purpose": "Post-traitement RELION de deux demi-cartes : FSC masquée corrigée par randomisation de phase, "
                   "affûtage par facteur B (ajustement de Guinier automatique ou donné), résolution locale en option.",
        "when": [
            "Rapporter une résolution et un facteur B « à la RELION » pour n'importe quelles demi-cartes.",
            "Carte affûtée de référence pour comparer avec LocScale, DeepEMhancer ou EMReady.",
        ],
        "avoid": ["Demi-cartes non indépendantes (après spIsoNet, carte composite) : la FSC serait surestimée."],
        "inputs": "Les demi-cartes non filtrées et un masque doux sur la même grille.",
        "tips": [
            "Facteur B automatique : ajustement à partir de 10 Å, sans objet sous ~8 Å de résolution.",
            "Le fichier MTF du détecteur (option avancée) corrige l'affûtage aux hautes fréquences.",
        ],
        "next": ["locscale", "modelangelo_build", "deposition_package"],
    },
    "relion_reconstruct": {
        "purpose": "Reconstruit les demi-cartes et la carte complète de particules à partir de leurs poses, sans alignement "
                   "(relion_reconstruct), et calcule la FSC : la carte pleine résolution d'un sous-ensemble.",
        "when": [
            "Sous-ensemble sélectionné dans l'espace latent de cryoDRGN : carte à pleine résolution de cet état.",
            "Vérifier rapidement des particules converties depuis CryoSPARC (même carte que le consensus ?).",
        ],
        "avoid": [
            "Particules sans demi-jeux : ils sont tirés au hasard et la FSC est optimiste (les deux moitiés ont été "
            "alignées ensemble) ; un raffinement local donne une vraie FSC gold-standard.",
        ],
        "inputs": "Particules avec poses (import CryoSPARC, sélection cryoDRGN, sortie d'un job RELION) ; masque facultatif "
                  "pour la FSC masquée.",
        "tips": ["Les demi-jeux de CryoSPARC (alignments3D/split) sont repris quand ils existent."],
        "next": ["relion_local_refine", "map_fsc", "locscale"],
    },
    "relion_reslog": {
        "purpose": "Résolution en fonction du nombre de particules, comme le ResLog de CryoSPARC : demi-cartes de "
                   "sous-ensembles aléatoires emboîtés (toutes, la moitié, le quart…) reconstruites avec les poses du "
                   "raffinement, leur résolution FSC masquée, et le facteur B de l'ajustement 1/d² = (2/B)·ln N + c "
                   "(Rosenthal & Henderson 2003).",
        "when": [
            "Décider s'il vaut la peine de collecter ou de garder plus de particules : résolution attendue avec deux fois "
            "plus, nombre de particules pour une résolution cible.",
            "Comparer deux jeux de données, deux nettoyages ou deux traitements (le plus petit facteur B l'emporte).",
        ],
        "avoid": [
            "Des particules sans demi-jeux du raffinement : tirés au hasard ici, les résolutions sont optimistes.",
            "Moins de quelques milliers de particules : trop peu de points au-dessus de Nyquist pour un ajustement fiable.",
        ],
        "inputs": "Les particules d'un raffinement (poses et demi-jeux) ; le masque du raffinement pour la FSC masquée.",
        "tips": [
            "Une droite plate (pas de facteur B) : la résolution est limitée par autre chose que le nombre de particules "
            "(hétérogénéité, précision des poses, CTF) ; essayer CTF refinement, polishing ou une classification.",
            "Chaque taille demande deux reconstructions : les processus MPI accélèrent les plus grandes.",
        ],
        "next": ["relion_ctf_refine", "relion_polish", "relion_class3d"],
    },
    # ------------------------------------------------- RELION : des films à la carte
    "import_coordinates": {
        "purpose": "Importe les positions de particules choisies par un autre logiciel et les associe aux micrographies du "
                   "projet par leur nom, comme l'import de coordonnées de RELION : fichiers STAR de RELION ou crYOLO (un par "
                   "micrographie, une liste autopick.star ou un fichier de particules), fichiers .box d'EMAN ou crYOLO, "
                   "picks de Topaz (.txt) ou de CryoSPARC (.cs).",
        "when": [
            "Le picking a été fait ailleurs (crYOLO, Topaz, CryoSPARC, un autre projet RELION).",
            "Reprendre des picks validés pour une nouvelle extraction (autre boîte, autre taille de pixel).",
        ],
        "avoid": ["Des picks faits sur d'autres micrographies que celles branchées (noms différents) : rien ne correspondra."],
        "inputs": "Les micrographies sur lesquelles les particules ont été choisies (avec CTF pour extraire ensuite).",
        "tips": [
            "Plusieurs fichiers ou jokers séparés par « ; », formats mélangés possibles (détectés par l'extension).",
            "« Coordinate scale » = 4 pour des picks faits sur des micrographies réduites 4 fois.",
            "Les .box donnent le coin de la boîte : le centre est calculé ; les .cs de CryoSPARC donnent des fractions de la micrographie.",
            "Le journal liste les fichiers qui ne correspondent à aucune micrographie.",
        ],
        "next": ["relion_extract", "relion_pick_inspect", "relion_topaz_train"],
    },
    "import_templates": {
        "purpose": "Importe des images 2D (une pile .mrcs ou des .mrc) comme modèles pour le picking par modèles, comme "
                   "« Import Templates » de CryoSPARC : moyennes de classes d'un autre logiciel ou projet, projections.",
        "when": ["Des moyennes 2D existent déjà (autre projet, autre logiciel) pour démarrer le picking par modèles."],
        "avoid": ["Des modèles d'une autre taille de pixel sans la renseigner : RELION les met à l'échelle avec la taille donnée."],
        "inputs": "Fichiers d'images (même taille, carrées).",
        "tips": [
            "RELION attend des particules blanches sur fond sombre (comme les moyennes de classes) : « Invert the contrast » sinon.",
            "Puis Auto-picking (RELION), méthode Templates, entrée 2D templates.",
        ],
        "next": ["relion_autopick"],
    },
    "relion_import": {
        "purpose": "Importe les films bruts (ou des micrographies déjà alignées) avec leur groupe optique : taille de pixel, "
                   "tension, aberration sphérique, contraste d'amplitude, gain et MTF. Écrit le movies.star de "
                   "relion_import ; les dossiers des films sont liés, rien n'est copié. Le point de départ du pipeline RELION.",
        "when": [
            "Démarrer un nouveau jeu de données à partir des films du microscope (TIFF, EER, MRC, MRC compressés).",
            "Reprendre dans RELION des micrographies déjà sommées par un autre logiciel.",
        ],
        "avoid": [
            "Particules déjà extraites ailleurs (CryoSPARC) : « Import particles » les prend directement.",
            "Sessions de tailles de pixel ou de détecteurs différents dans un seul import : importez-les séparément (un "
            "groupe optique chacune) puis réunissez-les avec « Join star files ».",
        ],
        "inputs": "Un motif (/data/session/Movies/*.tiff, « ** » pour les sous-dossiers) ou le dossier des films ; la "
                  "référence de gain quand les films ne sont pas corrigés.",
        "tips": [
            "Taille de pixel des films tels qu'enregistrés : en super-résolution, la taille super-résolution, et un "
            "binning de 2 dans la correction de mouvement.",
            "La dose par image (e⁻/Å²) sert à la pondération par la dose ; pour l'EER, la dose par fraction.",
            "Rotation et retournement du gain comme dans MotionCor2 (-RotGain / -FlipGain) : vérifiez l'orientation sur "
            "une première micrographie corrigée.",
            "Le fichier MTF du détecteur (wiki RELION) sert au post-traitement.",
        ],
        "next": ["relion_motioncorr", "relion_estimate_gain"],
    },
    "relion_estimate_gain": {
        "purpose": "Estime une référence de gain à partir des films eux-mêmes (relion_estimate_gain) : l'inverse de la "
                   "moyenne de milliers d'images (la moyenne des comptes pour l'EER). Les films ressortent avec ce gain "
                   "attaché, que la correction de mouvement applique.",
        "when": [
            "Référence de gain perdue, mal orientée ou d'une autre session ; motifs fixes visibles sur les micrographies.",
            "Films en mode comptage non corrigés du gain.",
        ],
        "avoid": [
            "Trop peu d'images : il en faut des milliers pour un gain sans bruit.",
            "Des films déjà corrigés du gain (le gain estimé serait plat et inutile).",
        ],
        "inputs": "Les films importés (non corrigés du gain).",
        "tips": [
            "5000 images tirées au hasard pour commencer ; -1 prend tous les films (long).",
            "EER : l'option --dont_invert est ajoutée d'office (le gain des EER est la moyenne des comptes).",
            "Vérifiez l'image du gain dans le rapport : pixels morts ou chauds, bandes du détecteur.",
        ],
        "next": ["relion_motioncorr"],
    },
    "relion_motioncorr": {
        "purpose": "Aligne les images de chaque film (mouvement induit par le faisceau, globalement puis par patchs) et les "
                   "somme avec pondération par la dose : l'implémentation CPU de RELION ou MotionCor2 sur GPU. Écrit aussi "
                   "les spectres de puissance sommés que lit CTFFIND et la trajectoire de chaque film (pour le polissage).",
        "when": [
            "Toujours, juste après l'import des films.",
            "Films super-résolution : binning 2 ici.",
        ],
        "avoid": [
            "Micrographies importées déjà sommées : passez directement à l'estimation de la CTF.",
            "MotionCor2 sans GPU : prenez l'implémentation de RELION (CPU).",
        ],
        "inputs": "Les films de « Import movies (RELION) ».",
        "tips": [
            "Patchs 5 × 5 pour K2, K3 ou Falcon 4 (1 × 1 = mouvement global seulement, le défaut du GUI de RELION).",
            "Implémentation RELION : un nombre de threads qui divise le nombre d'images ; chaque processus MPI traite un film.",
            "float16 divise la place disque par deux ; gardez les spectres de puissance pour CTFFIND.",
            "Le graphe du mouvement cumulé montre les films aberrants : retirez-les avec « Curate micrographs ».",
        ],
        "next": ["relion_ctffind"],
    },
    "relion_ctffind": {
        "purpose": "Ajuste la fonction de transfert de contraste de chaque micrographie avec CTFFIND 4.1 ou 5 (lancé par "
                   "relion_run_ctffind) : défocus, astigmatisme, déphasage (plaque de phase) et résolution jusqu'à laquelle "
                   "l'ajustement tient.",
        "when": ["Après la correction de mouvement, avant le picking : les particules extraites emportent leur CTF."],
        "avoid": ["Gctf n'est pas proposé : il ne lit pas le float16 et n'est plus maintenu."],
        "inputs": "Les micrographies de « Motion correction (RELION) ».",
        "tips": [
            "Résolution maximale 5 Å par défaut, 3-4 Å pour de bonnes données.",
            "Les spectres de puissance de la correction de mouvement donnent de meilleurs anneaux de Thon (indispensables "
            "pour des micrographies en float16).",
            "Le meilleur critère de tri est la résolution de l'ajustement : filtrez avec « Curate micrographs » (≤ 6 Å par exemple).",
            "L'image des ajustements (meilleur, médian, pire) permet de les vérifier d'un coup d'œil.",
            "Avancé : « Sample tilt search » (CTFFIND 4.1.15 ou plus récent) ajuste l'inclinaison de l'échantillon "
            "(collecte inclinée) ; « Extra ice statistics » ajoute la fraction de puissance et le kurtosis de l'anneau "
            "de glace (tri des micrographies de glace cristalline), avec les spectres de la correction de mouvement.",
        ],
        "next": ["relion_curate", "relion_autopick"],
    },
    "relion_live": {
        "purpose": "Traite les films pendant la collecte, comme CryoSPARC Live : à chaque cycle, les nouveaux films du "
                   "dossier sont importés, corrigés du mouvement, leur CTF estimée, triés sur des seuils et, au choix, "
                   "pickés et extraits (les jobs RELION de CryoPlug avec --only_do_unfinished). Le panneau suit la "
                   "session : comptes, cadence, CTF, défocus, mouvement, glace, expositions et picks ; « Stop after this "
                   "cycle » termine le job avec ses sorties.",
        "when": [
            "Pendant une session au microscope : juger la grille et la glace tout de suite, ajuster la collecte.",
            "Arriver à la fin de la collecte avec des micrographies triées et des particules prêtes pour la 2D.",
        ],
        "avoid": [
            "Un dossier où les films arrivent incomplets sans délai : réglez « A movie is complete after » (30 s par "
            "défaut) d'après la vitesse d'écriture.",
            "Des seuils de tri trop stricts au début : les micrographies rejetées restent dans « All micrographs » et se "
            "trient à nouveau ensuite.",
        ],
        "inputs": "Aucune : le motif des films (ou leur dossier), la taille de pixel, la dose par image et la référence de gain.",
        "tips": [
            "Picking LoG avec les diamètres minimum et maximum de la particule ; extraction à boîte réduite (64-128 px) "
            "pour une 2D rapide.",
            "Kill arrête tout sans sorties ; « Stop after this cycle » traite les derniers films puis donne les sorties.",
            "« Stop after no new movie for » arrête le job seul à la fin de la session.",
            "Limites d'acceptation en direct : tirez-les sur les histogrammes des expositions puis « Apply to the "
            "session » ; tout est retrié au cycle suivant. « Pause » fait attendre les films, « Resume » reprend.",
            "« Streaming 2D classification » : classes 2D des particules déjà extraites, relancées quand assez de "
            "nouvelles particules arrivent ; à l'arrêt, la galerie permet « Select these classes ».",
            "Depuis le job terminé, « Curate… » dans les expositions crée un tri interactif de toutes les micrographies.",
        ],
        "next": ["relion_curate", "relion_class2d", "relion_pick_inspect", "relion_extract"],
    },
    "relion_curate": {
        "purpose": "Trie les micrographies sur leur ajustement CTF, leur défocus, leur astigmatisme, leur mouvement et la glace "
                   "(comme « Manually Curate Exposures » de CryoSPARC). En mode interactif, le job attend : distributions "
                   "dont on tire les limites sur les histogrammes, nuage de points de deux mesures, tableau triable des "
                   "micrographies, et chaque micrographie avec son ajustement CTF et sa dérive, acceptée ou rejetée à la "
                   "main. Ou bien il applique d'un coup les seuils donnés. Les micrographies rejetées forment une seconde "
                   "sortie.",
        "when": [
            "Avant le picking : retirer glace épaisse, dérive, contamination, ajustements CTF ratés.",
            "Ne garder qu'une gamme de défocus pour un essai rapide.",
            "Depuis l'estimation de la CTF : « Curate… » dans le navigateur d'expositions crée ce job avec les limites essayées.",
        ],
        "avoid": ["Des seuils fixés à l'aveugle : regardez d'abord les histogrammes et quelques micrographies (mode interactif)."],
        "inputs": "Les micrographies avec CTF ; le mouvement et la dérive sont relus dans la correction de mouvement quand "
                  "l'estimation de la CTF les a perdus. En option, des particules ou des picks : leur nombre par "
                  "micrographie devient un critère et ceux des micrographies gardées forment une sortie.",
        "tips": [
            "Résolution d'ajustement CTF ≤ 5-8 Å selon les données ; mouvement total < 30-50 Å.",
            "Clavier : ← → pour passer d'une micrographie à l'autre (dans l'ordre et le filtre du tableau), A accepte, R "
            "rejette, U rend la décision aux limites ; double-clic sur un histogramme : plus de limite.",
            "Le travail est enregistré au fil de l'eau ; Done sans rien envoyer (agent) applique les seuils des paramètres.",
            "La sortie « rejected » permet de vérifier ce qui a été écarté.",
            "Avec les picks ou les particules connectés : écartez les micrographies trop chargées (agrégats, carbone) ou "
            "vides ; les particules des micrographies gardées suivent (sortie « particles » ou « coordinates »).",
        ],
        "next": ["relion_autopick", "relion_pick_inspect", "relion_topaz_train"],
    },
    "relion_autopick": {
        "purpose": "Repère les particules sur les micrographies (relion_autopick) : détection de blobs par Laplacien de "
                   "Gaussienne (sans modèle), correspondance avec des modèles (classes 2D ou projections d'une carte 3D) ou "
                   "Topaz (réseau de neurones, général ou entraîné sur ces données).",
        "when": [
            "Nouveau jeu de données : LoG pour une première série, puis les classes 2D comme modèles ou Topaz entraîné.",
            "Repicker tout le jeu avec un modèle Topaz entraîné sur les bonnes particules.",
        ],
        "avoid": [
            "Seuil LoG trop bas : beaucoup de faux positifs (glace, carbone) qui partent en 2D mais coûtent du temps.",
            "Modèles 3D d'un autre jeu de données (autre taille de pixel, autre contraste) sans vérification.",
        ],
        "inputs": "Les micrographies avec CTF ; selon la méthode, des classes 2D sélectionnées, une carte 3D ou un modèle Topaz.",
        "tips": [
            "LoG : diamètres minimum et maximum encadrant la particule ; ajustez le seuil (σ) d'après l'image des particules "
            "repérées, ou comparez plusieurs réglages d'un coup avec « Tune LoG picking ».",
            "Modèles 2D : recentrez les classes (option de « Select 2D classes ») ; distance minimale ≈ le diamètre.",
            "Topaz : le diamètre est obligatoire ; le seuil de figure de mérite se règle ensuite à l'extraction (-1 ou 0).",
            "L'histogramme des figures de mérite aide à choisir ce seuil ; « Inspect and edit… » le règle sur les micrographies.",
        ],
        "next": ["relion_extract", "relion_pick_inspect"],
    },
    "micrograph_junk": {
        "purpose": "Repère le « junk » de chaque micrographie, comme le « Micrograph Junk Detector » de CryoSPARC mais à "
                   "partir de la micrographie elle-même (sans réseau entraîné) : zones dont le fond est bien plus sombre "
                   "(contamination, glace épaisse, carbone) ou plus clair (trous) que le reste, ou traversé par une marche "
                   "nette (bords du film). Rejette les picks ou les particules qui y tombent, donne la part de junk de "
                   "chaque micrographie et garde les micrographies les plus propres.",
        "when": [
            "Après le picking (LoG, Topaz, importé) : retirer les particules sur la contamination et les bords du carbone "
            "avant l'extraction.",
            "Trier les micrographies sur leur part de junk.",
        ],
        "avoid": [
            "Des micrographies à peine plus grandes que quelques particules : le fond n'y est pas mesurable.",
            "Des seuils trop bas sur des grilles très chargées : un amas de particules peut passer pour du junk.",
        ],
        "inputs": "Les micrographies, et leurs picks ou des particules extraites de ces micrographies.",
        "tips": [
            "Les seuils sont des écarts robustes (médiane, MAD) du fond : 4 σ pour le sombre, 5 σ pour les bords ; "
            "baissez « Dark areas beyond » (2,5) pour rejeter aussi le carbone, activez « Bright » pour les trous.",
            "La pente d'épaisseur de glace sur la micrographie est retirée avant : elle n'est pas du junk.",
            "Chaque zone est prise jusqu'à la moitié du seuil autour de son cœur, puis élargie d'une marge (un rayon de "
            "particule par défaut).",
            "Le rapport montre les micrographies les plus touchées, junk en rouge, picks gardés en vert et rejetés en rouge.",
        ],
        "next": ["relion_extract", "relion_pick_inspect", "relion_curate"],
    },
    "relion_pick_tuner": {
        "purpose": "Essaie plusieurs réglages du picking par Laplacien de Gaussienne (plages de diamètres × seuils) sur "
                   "quelques micrographies, comme le « Blob Picker Tuner » de CryoSPARC, et montre côte à côte les "
                   "particules repérées par chacun avec leur nombre par micrographie ; « Pick with these settings » "
                   "prépare le picking de toutes les micrographies avec le réglage choisi.",
        "when": ["Nouveau jeu de données : trouver vite le diamètre et le seuil du LoG avant de tout picker."],
        "avoid": [
            "Juger sur le seul nombre de particules : regardez les images (glace, carbone, agrégats repérés aussi).",
            "Trop de réglages à la fois : 24 au plus ; affinez en deux tours (plages larges, puis serrées).",
        ],
        "inputs": "Les micrographies (de préférence avec leur CTF : les meilleurs ajustements sont essayés).",
        "tips": [
            "Les micrographies essayées sont prises dans la meilleure moitié des ajustements de CTF, réparties sur la "
            "session ; les images montrent les deux premières avec le cercle du diamètre moyen.",
            "Seuil plus bas : plus de particules (et de faux positifs) ; plus haut : moins.",
            "Le bouton d'une carte ouvre le picking de toutes les micrographies avec ces réglages, à vérifier puis lancer.",
        ],
        "next": ["relion_autopick", "relion_pick_inspect"],
    },
    "relion_pick_inspect": {
        "purpose": "Inspecte les particules repérées sur chaque micrographie et les corrige à la main (comme « Inspect Picks » "
                   "et « Manual Picker » de CryoSPARC) : seuil de figure de mérite et gamme de « power score » (puissance "
                   "locale autour de chaque pick) réglés sur leurs histogrammes, avec le nuage des deux, particules retirées "
                   "ou ajoutées d'un clic, zoom, déplacement, filtre passe-bas de l'affichage. Sans coordonnées en entrée, "
                   "c'est un picking entièrement manuel ; en mode « Filaments », on clique le début puis la fin de chaque "
                   "filament, comme le picking manuel d'hélices de RELION.",
        "when": [
            "Après le picking automatique : choisir le seuil de figure de mérite en regardant les micrographies.",
            "Écarter les picks sur la glace cristalline, le carbone et la contamination (power score élevé) ou sur la glace "
            "vide (power score faible).",
            "Picker à la main quelques centaines de particules pour entraîner Topaz ou faire de premières classes 2D.",
            "Filaments, fibres amyloïdes, hélices : tracer les tubes, puis extraction avec « Helical processing » qui les "
            "découpe en segments.",
            "Vérifier qu'un picking n'a pas pris la glace, le carbone ou les bords des trous.",
        ],
        "avoid": [
            "Corriger des milliers de micrographies une à une : réglez plutôt le seuil puis nettoyez en 2D.",
            "Un seuil choisi sur une seule micrographie : passez-en plusieurs (flèches ← →).",
        ],
        "inputs": "Les micrographies (avec CTF) et, si on inspecte un picking, ses coordonnées ; le diamètre est obligatoire "
                  "pour un picking manuel.",
        "tips": [
            "Glissez sur l'histogramme pour le seuil ; « Hidden » montre en pointillés les particules sous le seuil et en rouge "
            "celles retirées.",
            "Le travail est enregistré au fil de l'eau : on peut fermer la page et revenir avant Done.",
            "Les particules ajoutées à la main reçoivent une figure de mérite au-dessus de toutes les autres : un seuil "
            "appliqué plus tard les garde.",
            "Depuis un picking terminé, « Inspect and edit… » crée directement ce job avec le seuil affiché.",
            "Power score : les deux poignées de son histogramme (échelle log quand il s'étale) gardent une gamme ; le nuage "
            "figure de mérite / power score montre la boîte gardée. Le calcul lit chaque micrographie une fois "
            "(désactivable pour de très gros jeux).",
            "Filaments : le diamètre est celui du tube ; un clic sur un filament le retire, Échap annule un début. "
            "L'extraction refuse ces coordonnées sans « Helical processing » (montée, diamètre du tube) qui les découpe.",
        ],
        "next": ["relion_extract", "relion_topaz_train"],
    },
    "relion_topaz_denoise": {
        "purpose": "Débruite les micrographies avec le réseau pré-entraîné de Topaz (topaz denoise), comme le « Micrograph "
                   "Denoiser » de CryoSPARC : les particules ressortent pour le picking à l'œil ou automatique et pour "
                   "l'inspection. Les picks faits sur les micrographies débruitées s'extraient des originales : "
                   "l'extraction traduit les noms.",
        "when": [
            "Particules peu contrastées, petites ou en glace épaisse : picking manuel ou LoG sur micrographies débruitées.",
            "Préparer un jeu d'entraînement Topaz par picking manuel.",
        ],
        "avoid": ["Estimer la CTF ou extraire sur les micrographies débruitées : le bruit y est modifié ; utilisez les originales."],
        "inputs": "Les micrographies corrigées du mouvement (avec CTF).",
        "tips": [
            "« Micrographs » : quelques dizaines suffisent pour juger ou entraîner un picking.",
            "GPU conseillé (sur CPU, plusieurs secondes par micrographie).",
            "Connectez à l'extraction les micrographies originales et les picks faits sur les débruitées.",
        ],
        "next": ["relion_pick_inspect", "relion_autopick"],
    },
    "relion_topaz_train": {
        "purpose": "Entraîne un réseau Topaz sur ce jeu de données (relion_autopick --topaz_train) à partir de particules "
                   "propres (celles des bonnes classes 2D) ou de coordonnées vérifiées ; le modèle sert ensuite à repicker "
                   "tout le jeu.",
        "when": [
            "Particules difficiles pour le LoG : petites, irrégulières, peu contrastées.",
            "Après une première classification 2D : les bonnes particules montrent au réseau à quoi ressemble la protéine.",
        ],
        "avoid": [
            "Particules d'entraînement non nettoyées : le réseau apprend aussi la glace et le carbone.",
            "Sans GPU : l'entraînement est très lent.",
        ],
        "inputs": "Des micrographies (quelques centaines suffisent) et les particules sélectionnées (ou des coordonnées).",
        "tips": [
            "Particules par micrographie : une estimation large de la moyenne réelle.",
            "Le modèle gardé est celui de l'époque à la meilleure précision de test (AUPRC), tracée dans le rapport.",
        ],
        "next": ["relion_autopick"],
    },
    "relion_extract": {
        "purpose": "Découpe les particules dans les micrographies (relion_preprocess --extract) avec leur CTF : boîte, "
                   "réduction d'échelle, inversion de contraste et normalisation comme RELION les attend. Ré-extrait aussi "
                   "des particules raffinées (recentrées, autre boîte ou autre taille de pixel).",
        "when": [
            "Après le picking.",
            "Après un raffinement à taille de pixel réduite : ré-extraction à pleine échelle, recentrée sur les positions raffinées.",
        ],
        "avoid": ["Boîte trop serrée : comptez 1,5 à 2 fois le diamètre (la CTF délocalise le signal hors de la particule)."],
        "inputs": "Les micrographies avec CTF et les coordonnées du picking (ou des particules à ré-extraire).",
        "tips": [
            "Réduire l'échelle (256 → 64 px par exemple) accélère la 2D et le modèle initial ; ré-extrayez à pleine "
            "échelle avant le raffinement final.",
            "Boîtes paires uniquement ; la boîte réduite aussi.",
            "Topaz : un seuil de figure de mérite (-1 par exemple) écarte les mauvais picks.",
            'Filaments : « Helical processing » découpe les filaments en segments (diamètre du tube, montée, unités asymétriques par segment).',
        ],
        "next": ["relion_class2d", "relion_initial_model", "relion_refine3d"],
    },
    "relion_class2d": {
        "purpose": "Trie les particules en moyennes de classes 2D (relion_refine) : VDAM, l'algorithme par gradient rapide "
                   "de RELION 4/5, ou l'EM classique. Les moyennes s'affichent pendant le calcul ; les particules gardent "
                   "leur classe pour la sélection des bonnes classes.",
        "when": [
            "Nettoyer les particules après le picking : glace, carbone, agrégats, particules cassées.",
            "Voir les vues de la particule et choisir des modèles de picking.",
        ],
        "avoid": [
            "VDAM avec plusieurs processus MPI : non supporté (des threads à la place) ; l'EM accepte le MPI.",
            "Trop peu de classes pour un grand jeu : 100-200 classes isolent mieux les vues rares.",
        ],
        "inputs": "Les particules extraites (RELION ou CryoSPARC).",
        "tips": [
            "VDAM, 200 mini-lots : bons résultats pour la plupart des données ; 50 à 200 classes.",
            "Masque circulaire un peu plus grand que la particule (0 = 90 % de la boîte).",
            "T = 2 en 2D ; limiter la résolution de l'étape E (7-12 Å) évite le sur-ajustement des petites particules.",
            "Dans la galerie, cliquez des classes puis « Select these classes » : la sélection est créée et lancée.",
            "Filaments : « Helical processing » (diamètre du tube, recherche bimodale de ψ, décalages limités le long de l'axe).",
            "Les classes suffisent avant la fin ? « Stop », puis « Mark as complete » : les moyennes et les particules de "
            "la dernière itération écrite deviennent les sorties (VDAM écrit toutes les 10 itérations). « Resume » "
            "relance un run arrêté de sa dernière itération.",
        ],
        "next": ["relion_select2d", "relion_class2d", "relion_particle_curate"],
    },
    "relion_select2d": {
        "purpose": "Garde les particules des bonnes classes 2D. Comme dans CryoSPARC, le job affiche les moyennes et attend : "
                   "cliquez les bonnes classes (triées par taille ou résolution), puis Done. Sélection aussi par liste, "
                   "automatique (class ranker de RELION) ou par corrélation avec les projections d'une carte de référence "
                   "(le « Reference Based Auto Select 2D » de CryoSPARC). Donne les particules gardées, les rejetées et les "
                   "moyennes sélectionnées (modèles de picking).",
        "when": [
            "Après chaque classification 2D.",
            "Préparer des modèles 2D pour le picking par correspondance de modèles (option de recentrage).",
            "Sélection automatique dans une chaîne de traitement (score minimum du class ranker).",
            "Une carte de la particule existe déjà (raffinement précédent, carte d'un modèle) : sélection par référence, "
            "qui garde les classes ressemblant à une de ses vues.",
        ],
        "avoid": ["Ne garder que les plus belles classes : les vues rares sont souvent dans des classes moins nettes ; "
                  "préférez plusieurs tours de 2D."],
        "inputs": "Les moyennes de classes d'une « 2D classification (RELION) ».",
        "tips": [
            "Maj + clic sélectionne une série de classes ; boutons Tout, Aucune, Inverser.",
            "« Au plus N particules par classe » équilibre les vues avant un modèle initial.",
            "Class ranker : score de 0 à 1 (0,5 par défaut) ; il demande l'environnement Python de RELION 5.",
            "« By thresholds… » coche d'un coup les classes d'au moins N particules, à résolution donnée ou meilleure "
            "(et score du class ranker).",
            "Par référence : chaque classe est comparée, à 15 Å, aux projections de la carte (directions couvrant la "
            "sphère, rotations et décalages dans le plan) ; la corrélation de chaque classe s'affiche sur la galerie, avec "
            "les classes à côté de la projection qui leur ressemble le plus. Une carte de l'autre main donne les mêmes "
            "corrélations (les projections couvrent les deux mains).",
        ],
        "next": ["relion_initial_model", "relion_class2d", "relion_class3d_global", "relion_refine3d", "relion_topaz_train",
                 "relion_autopick"],
    },
    "relion_select3d": {
        "purpose": "Garde les particules des classes 3D cliquées dans une galerie des cartes de classes (projections selon "
                   "z, y, x et coupe centrale), ou listées : réunit les bonnes classes d'une classification 3D ou d'un "
                   "raffinement hétérogène ; les autres forment une seconde sortie.",
        "when": [
            "Après une classification 3D : réunir les classes de la bonne conformation avant le raffinement.",
            "Après un raffinement hétérogène avec leurres : garder la classe de la protéine.",
        ],
        "avoid": ["Des classes de conformations différentes réunies : la carte moyenne les mélange."],
        "inputs": "Les « Classified particles » d'une classification 3D ou d'un raffinement hétérogène (elles portent le "
                  "modèle des classes).",
        "tips": [
            "Depuis la galerie d'une classification terminée, « Select these classes » crée et lance ce job.",
            "Ouvrez les cartes des classes (sorties map_N) dans le visualiseur 3D pour juger les détails.",
        ],
        "next": ["relion_refine3d", "relion_class3d_global"],
    },
    "relion_initial_model": {
        "purpose": "Calcule une première carte 3D sans référence (relion_refine --grad --denovo_3dref, VDAM) puis l'aligne "
                   "sur les axes de symétrie et applique la symétrie (relion_align_symmetry) : l'ab initio de RELION.",
        "when": [
            "Aucune carte de départ : après une sélection 2D propre.",
            "Plusieurs classes : séparer dès le départ des structures différentes.",
        ],
        "avoid": ["Vues préférentielles extrêmes : le modèle sera déformé (regardez la distribution angulaire ensuite)."],
        "inputs": "Quelques milliers à quelques dizaines de milliers de bonnes particules de vues variées (à taille de "
                  "pixel réduite pour aller vite).",
        "tips": [
            "Calcul en C1 puis symétrie appliquée : plus robuste que la symétrie imposée dès le départ.",
            "T = 4 et 200 mini-lots, comme le GUI de RELION.",
            "La carte est sur l'échelle de gris absolue de RELION : les jobs 3D suivants s'en servent sans --firstiter_cc.",
            "Arrêté ou tombé en route : « Resume » reprend de la dernière itération, « Mark as complete » aligne et "
            "symétrise le modèle de cette itération.",
        ],
        "next": ["relion_class3d_global", "relion_hetero_refine", "relion_refine3d"],
    },
    "relion_class3d_global": {
        "purpose": "Trie les particules en classes 3D contre une référence (relion_refine --K) : recherches angulaires "
                   "globales, locales ou aucune (classification à poses fixes). Chaque classe donne une carte et ses "
                   "particules ; Blush aide les particules petites ou flexibles.",
        "when": [
            "Nettoyer encore les particules après la 2D (particules cassées, mauvaises conformations).",
            "Séparer des états discrets : ligand lié ou libre, conformations ouverte et fermée.",
        ],
        "avoid": [
            "Hétérogénéité continue : multi-corps, DynaMight, cryoDRGN ou la 3D variability de CryoSPARC la décrivent mieux.",
            "Référence trop détaillée : filtre passe-bas initial de 40 à 60 Å pour un modèle initial.",
        ],
        "inputs": "Les particules (sélection 2D) et une référence (modèle initial ou carte raffinée) ; un masque en option.",
        "tips": [
            "4 à 8 classes, 25 itérations, T = 4.",
            "Sans alignement, sur des particules raffinées et avec un masque : classification rapide et focalisée.",
            "Les cartes des classes se jouent comme une série dans le visualiseur 3D.",
            "Le rapport suit la part de chaque classe au fil des itérations (convergence) et montre les directions de "
            "vue de chaque classe.",
            'Filaments : « Helical processing » avec twist et montée initiaux (et leur recherche locale).',
            "Un run en échec ou tué (mémoire, nœud perdu, temps de file dépassé) se relance de sa dernière itération : "
            "« Resume » (le « Continue » de RELION, fichiers gardés, sorties sous run_ct<itération>) ; « Mark as "
            "complete » le termine avec les sorties de cette itération (comme dans CryoSPARC).",
        ],
        "next": ["relion_refine3d", "relion_class3d_global", "relion_hetero_refine", "relion_particle_curate"],
    },
    "relion_hetero_refine": {
        "purpose": "Répartit les particules entre plusieurs cartes de référence tout en les raffinant toutes (comme le "
                   "« Heterogeneous Refinement » de CryoSPARC ; relion_refine avec une référence par classe). Les cartes "
                   "venues d'autres jobs sont rééchantillonnées sur la grille des particules.",
        "when": [
            "Nettoyer un jeu de particules : une bonne carte et des leurres (carte de déchets, particule cassée, micelle "
            "vide) qui attirent les mauvaises particules.",
            "Après un modèle initial à plusieurs classes : séparer conformations ou compositions avec ces classes comme "
            "références.",
        ],
        "avoid": [
            "Des références identiques : elles se partagent les particules au hasard ; partez de cartes réellement différentes.",
            "Des références trop détaillées : le filtre passe-bas initial (40 Å par défaut) évite le biais de modèle.",
        ],
        "inputs": "Les particules, au moins deux cartes (une par classe), un masque en option.",
        "tips": [
            "Leurres : une carte de bruit filtrée, une classe « poubelle » d'une classification, une carte d'un mauvais "
            "modèle initial.",
            "Le rapport donne la part de chaque classe au fil des itérations, leur résolution estimée et les directions de "
            "vue par classe ; les particules de chaque classe sont des sorties.",
        ],
        "next": ["relion_refine3d", "relion_class3d_global"],
    },
    "relion_refine3d": {
        "purpose": "Raffinement 3D gold-standard (relion_refine --auto_refine) : recherches globales puis locales à mesure "
                   "qu'il converge, deux demi-jeux indépendants. Donne la carte, les demi-cartes, la FSC, les particules "
                   "raffinées et, avec un masque, la carte post-traitée.",
        "when": [
            "Après la classification : la carte haute résolution.",
            "Après une ré-extraction à pleine échelle, la CTF refinement ou le polissage : un nouveau raffinement.",
        ],
        "avoid": ["Moins de 3 processus MPI : impossible (un maître et un processus par demi-jeu)."],
        "inputs": "Les particules et une référence (classe 3D, modèle initial ou carte raffinée) ; un masque doux "
                  "(« Mask creation (RELION) ») pour la FSC corrigée et le post-traitement.",
        "tips": [
            "Filtre initial 40-60 Å pour un modèle initial, 10-15 Å pour une carte déjà raffinée à partir des mêmes particules.",
            "Recherches locales à partir de 1,8° (le défaut du GUI de RELION).",
            "« Solvent-flattened FSC » (avec un masque) aide une petite particule dans une grande boîte.",
            "Blush (RELION 5) pour les particules petites ou flexibles.",
            "Filaments : « Helical processing » impose la symétrie hélicoïdale (twist, montée) et peut l'affiner (recherche locale).",
            "Un run en échec ou tué (mémoire, nœud perdu, temps de file dépassé) se relance de sa dernière itération : "
            "« Resume » (le « Continue » de RELION, fichiers gardés, sorties sous run_ct<itération>) ; « Mark as "
            "complete » le termine avec les sorties de cette itération (comme dans CryoSPARC).",
        ],
        "next": ["relion_mask_create", "relion_ctf_refine", "relion_polish", "relion_postprocess", "relion_locres",
                 "relion_multibody", "relion_dynamight", "relion_local_refine"],
    },
    "relion_mask_create": {
        "purpose": "relion_mask_create : la carte filtrée passe-bas, seuillée, dilatée de quelques pixels et adoucie par un "
                   "bord en cosinus. Le masque de solvant du post-traitement, du raffinement et de la CTF refinement.",
        "when": ["Après le premier raffinement 3D, pour le post-traitement et les raffinements suivants."],
        "avoid": [
            "Seuil trop haut : le masque coupe de la densité, la FSC est surestimée.",
            "Carte affûtée en entrée : prenez la carte non affûtée du raffinement.",
        ],
        "inputs": "Une carte raffinée non affûtée.",
        "tips": [
            "Filtre 15 Å, extension 3-5 px, bord doux 6-10 px.",
            "Seuil 0 = automatique (seuil d'Otsu de la carte filtrée) ; vérifiez le masque sur la carte dans le visualiseur.",
            "Masque dessiné à la main ou tiré d'un modèle : « Mask drawn in the 3D viewer » ou « Create mask ».",
            "Filaments : « Helical processing » garde la partie centrale le long de l'axe (z %).",
        ],
        "next": ["relion_refine3d", "relion_ctf_refine", "relion_polish", "relion_postprocess", "relion_locres"],
    },
    "relion_helix_inimodel2d": {
        "purpose": "Construit le modèle initial 3D d'une fibre amyloïde à partir de ses classes 2D "
                   "(relion_helix_inimodel2d, tutoriel amyloïdes de RELION) : les moyennes de classes sont alignées en "
                   "une longue image 2D couvrant tout un cross-over, puis tournées autour de l'axe selon la distance "
                   "entre cross-overs. Une carte par classe, référence du raffinement hélical 3D.",
        "when": [
            "Fibres amyloïdes sans modèle initial : après une classification 2D de segments en grandes boîtes.",
            "Plusieurs polymorphes : une carte par classe (« Classes »).",
        ],
        "avoid": [
            "Des classes 2D floues ou de petite boîte : il faut voir la torsion de la fibre sur les classes.",
            "Une distance de cross-over approximative : mesurez-la sur les micrographies ou de longues classes.",
        ],
        "inputs": "Les bonnes classes 2D (Select 2D classes) ou les classes d'une classification 2D (les vides sont "
                  "écartées).",
        "tips": [
            "Symétrie 2 dans la section pour deux protofilaments reliés par un axe d'ordre 2 (pseudo-2₁).",
            "Puis raffinement hélical 3D avec ce modèle (twist ≈ −180 × montée / cross-over, montée 4,75 Å).",
            "Recherche de décalage ~10 Å et d'angle ~15° ; quelques dizaines d'itérations suffisent.",
        ],
        "next": ["relion_refine3d", "relion_class3d_global", "relion_helix_toolbox"],
    },
    "relion_align_symmetry": {
        "purpose": "Tourne (et recentre) une carte raffinée sans symétrie pour que ses axes de symétrie soient ceux de RELION "
                   "(relion_align_symmetry), comme les « Volume Alignment Tools » de CryoSPARC : la référence d'un "
                   "raffinement symétrique ou d'une expansion de symétrie. La symétrie peut aussi être appliquée.",
        "when": [
            "Après un raffinement C1 qui montre une symétrie : aligner la carte avant de raffiner avec la symétrie.",
            "Avant une expansion de symétrie, avec une carte venue d'un autre logiciel ou d'une autre convention.",
        ],
        "avoid": ["Une symétrie supposée sans la voir dans la carte C1 : vérifiez d'abord (classes 3D, carte C1)."],
        "inputs": "Une carte (raffinée en C1, de préférence non affûtée).",
        "tips": [
            "Boîte de travail 64 px (Nyquist ~20 Å) : suffisant et rapide.",
            "Puis 3D auto-refine avec cette carte et la symétrie (filtre initial 10-15 Å : la carte vient de ces particules).",
            "« Rotation about z only » quand l'axe principal est déjà sur z (Cn).",
        ],
        "next": ["relion_refine3d", "relion_symmetry_expand", "relion_mask_create"],
    },
    "relion_symmetry_search": {
        "purpose": "Trouve le groupe ponctuel d'une carte raffinée en C1, comme le « Symmetry Search Utility » de CryoSPARC : "
                   "pour chaque groupe essayé (Cn, Dn, T, O, I), relion_align_symmetry tourne la carte sur les axes de "
                   "RELION de ce groupe, puis la carte est comparée à sa copie symétrisée par les rotations du groupe "
                   "(corrélation à la résolution choisie). Propose le plus grand groupe que la carte respecte, avec la "
                   "carte alignée sur ses axes.",
        "when": [
            "Une carte C1 (modèle initial, premier raffinement) dont la symétrie est inconnue ou douteuse.",
            "Vérifier une symétrie supposée avant de l'imposer au raffinement.",
        ],
        "avoid": [
            "Une carte très bruitée ou à basse résolution : la corrélation des groupes faux monte aussi ; comparez à une "
            "résolution plus grossière et regardez l'écart entre groupes.",
            "Imposer le groupe proposé sans regarder la carte : une pseudo-symétrie (sous-unités presque identiques) "
            "donne aussi une forte corrélation.",
        ],
        "inputs": "Une carte raffinée en C1, de préférence non affûtée.",
        "tips": [
            "Les sous-groupes d'un groupe respecté le sont aussi (C2 pour une carte C4) : le plus grand groupe au-dessus du "
            "seuil est proposé ; le tableau et le graphique (corrélation contre n pour Cn et Dn) montrent tous les groupes.",
            "Résolution de comparaison : environ deux fois la résolution de la carte (8 Å par défaut).",
            "Les opérateurs sont ceux de RELION (src/symmetries.cpp : Cn selon z, Dn avec un axe 2 selon x, I = I2) : la "
            "carte alignée sert directement de référence à un 3D auto-refine avec ce groupe.",
        ],
        "next": ["relion_refine3d", "relion_align_symmetry", "relion_symmetry_expand"],
    },
    "align_3d_maps": {
        "purpose": "Amène une carte dans le repère d'une carte de référence (rotation et translation), comme « Align 3D "
                   "maps » de CryoSPARC, et fait suivre ses demi-cartes, son masque et les poses de ses particules : des "
                   "particules raffinées contre des références différentes se retrouvent dans un même repère.",
        "when": [
            "Réunir deux jeux de données raffinés séparément (puis Join star files et un raffinement commun).",
            "Comparer ou combiner des classes 3D, des cartes de deux logiciels, un modèle ab initio et une carte consensus.",
            "Remettre un masque dessiné sur une carte dans le repère d'une autre.",
        ],
        "avoid": [
            "Des cartes de conformations très différentes : l'alignement suit la partie la plus massive ; masquez la partie commune.",
            "Les priors d'angles (filaments) ne suivent pas : refaites un raffinement hélical ensuite.",
        ],
        "inputs": "La carte à aligner et la référence ; facultatifs : ses demi-cartes, son masque, ses particules (celles de "
                  "son raffinement).",
        "tips": [
            "Recherche globale par défaut (toutes les orientations, les meilleures affinées) ; « Local » pour des cartes déjà proches.",
            "« Also try the mirror image » pour un modèle ab initio de main inconnue : les poses sont alors aussi inversées.",
            "Corrélation après alignement > 0,8 : alignement sûr ; regardez aussi les coupes centrales avant / après.",
            "Les sorties sont sur la grille de la référence (boîte et pixel), centres des boîtes alignés.",
        ],
        "next": ["relion_join", "relion_refine3d", "relion_reconstruct", "map_tools", "relion_local_refine"],
    },
    "recentre_particles": {
        "purpose": "Amène une région d'une carte au centre de la boîte et change les décalages de ses particules de la même "
                   "façon, comme les « Volume Alignment Tools » de CryoSPARC : centre de masse d'un masque (un domaine, une "
                   "sous-unité), de la carte, ou un point donné. Ré-extraites, les particules sont centrées sur la région.",
        "when": [
            "Avant un raffinement ou une classification focalisés sur un domaine excentré, dans une boîte plus petite.",
            "Une carte décentrée dans sa boîte (particules extraites sur un mauvais centre).",
        ],
        "avoid": ["Retirer le reste du complexe : c'est la soustraction de signal (RELION signal subtraction)."],
        "inputs": "La carte, le masque de la région (pour son centre de masse) et les particules du raffinement de la carte.",
        "tips": [
            "Puis Particle extraction avec « Particles to re-extract » : la ré-extraction recentre sur les nouveaux décalages.",
            "Un point lu dans le visualiseur 3D (coordonnées en voxels depuis le coin de la boîte) avec « A point ».",
        ],
        "next": ["relion_extract", "relion_local_refine", "relion_class3d"],
    },
    "relion_simulate": {
        "purpose": "Fabrique des images de particules à partir d'une carte (relion_project), comme « Simulate Data » de "
                   "CryoSPARC : projections en orientations aléatoires avec bruit blanc, avec les orientations et les CTF de "
                   "vraies particules, ou données réalistes (le signal de vraies particules remplacé par des projections de "
                   "la carte dans de nouvelles orientations, la vérité terrain gardée dans le fichier).",
        "when": [
            "Tester une chaîne de traitement, un masque ou une méthode d'hétérogénéité sur des données dont on connaît la réponse.",
            "Vérifier qu'une classification sépare bien deux états simulés (deux cartes simulées puis réunies).",
        ],
        "avoid": [
            "Présenter des particules simulées comme des données : elles servent au contrôle uniquement.",
            "Des particules sans orientations (sortie d'extraction) : les modes « particules » et réaliste exigent des "
            "particules raffinées contre cette carte.",
        ],
        "inputs": "Une carte ; facultatif : des particules raffinées contre elle (orientations et CTF, et leurs images "
                  "pour le mode réaliste, même boîte que la carte).",
        "tips": [
            "Mode réaliste : RELION soustrait de chaque image la projection de la carte dans l'orientation de la particule, "
            "puis ajoute une projection dans l'orientation d'une autre particule ; prendre la carte non affûtée du "
            "raffinement (même échelle de gris que les images).",
            "Mode réaliste : « Signal-to-noise relative to the data » = 1 garde le rapport signal/bruit des données.",
            "Join star files réunit des particules simulées de deux cartes pour tester une classification 3D.",
        ],
        "next": ["relion_class2d", "relion_refine3d", "relion_class3d_global", "relion_join"],
    },
    "check_particles": {
        "purpose": "Lit chaque image de particule et met de côté celles qui arrêteraient ou fausseraient un job, comme « Check "
                   "For Corrupt Particles » de CryoSPARC : piles ou images manquantes, mauvaise taille de boîte, valeurs NaN "
                   "ou infinies, images vides, et en option les valeurs extrêmes de moyenne ou d'écart-type.",
        "when": [
            "Un raffinement ou une classification s'arrête sur une erreur de lecture d'image ou des NaN.",
            "Des particules importées d'ailleurs (autre disque, autre logiciel) dont on n'est pas sûr que toutes les piles soient là.",
            "Après une extraction interrompue ou un disque plein.",
        ],
        "avoid": ["Trier les bonnes et mauvaises particules sur leur contenu : c'est le rôle des classifications et de « Curate particles »."],
        "inputs": "Des particules (RELION ou CryoSPARC).",
        "tips": [
            "« Outliers beyond » à 8-10 σ ne retire que les images aberrantes (zones mortes, bords de carbone, cristaux de glace) ; 0 = seulement les images inutilisables.",
            "Les chemins des images sont réécrits par rapport au dossier du projet : la sortie se lit depuis n'importe quel job.",
        ],
        "next": ["relion_class2d", "relion_refine3d", "relion_restack", "relion_particle_curate"],
    },
    "average_power_spectra": {
        "purpose": "Spectres de puissance des particules de chaque classe 2D, tournés dans le repère de la classe et moyennés, "
                   "comme « Average Power Spectra » de CryoSPARC : les lignes de couche d'un filament y ressortent bien mieux "
                   "que dans le spectre de la moyenne de classe, pour indexer sa symétrie hélicale (torsion et montée).",
        "when": [
            "Filaments (amyloïdes, filaments protéiques) après une classification 2D : estimer la montée et la torsion.",
            "Vérifier qu'une classe contient un seul type de filament (lignes de couche nettes et cohérentes).",
        ],
        "avoid": ["Des particules globulaires : il n'y a pas de lignes de couche à voir."],
        "inputs": "Les particules d'une classification 2D (classe et angle psi) ; facultatif : les classes 2D, montrées à côté.",
        "tips": [
            "Choisissez les classes droites et nettes (« Classes ») ; 500 particules par classe suffisent souvent.",
            "La première ligne de couche se lit à 1/montée (Å⁻¹) le long de l'axe ; puis Helical symmetry (RELION) affine twist et montée.",
        ],
        "next": ["relion_helix_toolbox", "relion_helix_inimodel2d", "relion_select2d"],
    },
    "relion_helix_toolbox": {
        "purpose": "Outils de symétrie hélicale de RELION (relion_helix_toolbox) sur la carte d'un filament : recherche "
                   "locale de la torsion (twist) et de la montée (rise), puis imposition de la symétrie trouvée dans "
                   "l'espace réel ; imposition d'une symétrie donnée ; ou cylindre comme référence initiale d'un "
                   "raffinement hélical.",
        "when": [
            "Après un premier raffinement hélical : affiner twist et rise dans une petite fenêtre autour des valeurs "
            "connues (diffraction, classes 2D).",
            "Symétriser une carte de filament pour la construction de modèle.",
            "Pas de référence pour un raffinement hélical : un cylindre du diamètre du tube.",
        ],
        "avoid": [
            "Une fenêtre de recherche large : la recherche est locale, plusieurs symétries peuvent expliquer une carte.",
            "Une valeur trouvée au bord de la fenêtre : élargissez la fenêtre (le job prévient).",
        ],
        "inputs": "La carte d'un raffinement hélical (recherche, imposition) ; rien pour le cylindre.",
        "tips": [
            "Twist positif : hélice droite ; vérifiez la main avec un modèle ou des hélices α visibles.",
            "Partie centrale à 30 % de la hauteur de la boîte : les extrémités d'une reconstruction sont moins bonnes.",
            "Le cylindre se fait à la taille de pixel et à la boîte des particules du raffinement.",
        ],
        "next": ["relion_refine3d", "relion_mask_create", "relion_postprocess"],
    },
    "relion_ctf_refine": {
        "purpose": "Affine l'optique du jeu de données et la CTF de chaque particule contre la carte raffinée "
                   "(relion_ctf_refine) : grandissement anisotrope, tilt du faisceau, aberrations d'ordre 3 et 4 par groupe "
                   "optique, puis défocus par particule et astigmatisme par micrographie. « Complete » enchaîne les trois "
                   "passes du tutoriel de RELION.",
        "when": [
            "Résolution meilleure que ~4,5 Å : le gain est net.",
            "Tilt du faisceau (coma) ou grandissement anisotrope suspectés.",
        ],
        "avoid": ["Carte à basse résolution (au-delà de 6 Å) : les ajustements sont mal contraints."],
        "inputs": "Les particules d'un « 3D auto-refine (RELION) », ses demi-cartes et un masque doux.",
        "tips": [
            "Le job fait lui-même le post-traitement (FSC des demi-cartes) dont relion_ctf_refine a besoin.",
            "Raffinez ensuite les particules à nouveau : la résolution doit s'améliorer.",
            "Le tableau des groupes optiques montre les paramètres ajustés (tilt, Zernike, matrice de grandissement).",
        ],
        "next": ["relion_refine3d", "relion_polish"],
    },
    "relion_polish": {
        "purpose": "Suit le mouvement de chaque particule à travers les images du film et la ré-extrait avec des poids par "
                   "image ajustés aux données (relion_motion_refine) : le polissage bayésien. Entraîner une fois les "
                   "paramètres sur un sous-ensemble (facultatif), puis polir ; raffiner ensuite les particules « shiny ».",
        "when": ["Après la CTF refinement, pour les derniers dixièmes d'Å."],
        "avoid": ["Micrographies importées déjà sommées : il faut les films et la correction de mouvement de RELION."],
        "inputs": "Les particules raffinées, les micrographies de la correction de mouvement (ou n'importe quelles "
                  "micrographies qui en descendent : le mouvement y est retrouvé), les demi-cartes et un masque.",
        "tips": [
            "Entraînement : environ 10 000 particules suffisent ; branchez ses paramètres sur le job de polissage "
            "(entrée « Trained parameters »).",
            "Sans entraînement : sigmas par défaut 0,2 / 5000 / 2.",
            "Le facteur B par image montre la perte de signal au fil de l'exposition.",
        ],
        "next": ["relion_refine3d"],
    },
    "relion_locres": {
        "purpose": "Résolution locale avec relion_postprocess --locres : FSC dans une sphère douce déplacée sur la carte. "
                   "Donne la carte de résolution locale (pour colorer la carte dans le visualiseur) et la carte filtrée "
                   "localement.",
        "when": ["Repérer les régions flexibles ou mal définies après un raffinement."],
        "avoid": ["Demi-cartes non indépendantes (spIsoNet, carte composite) : la résolution locale serait surestimée."],
        "inputs": "Les demi-cartes d'un raffinement ; un masque en option (statistiques dans le masque).",
        "tips": [
            "Facteur B : celui du post-traitement convient.",
            "« Local resolution » (Phenix) donne une estimation indépendante à comparer.",
        ],
        "next": ["locscale", "modelangelo_build", "relion_multibody"],
    },
    "relion_join": {
        "purpose": "Réunit deux à quatre jeux de particules, de micrographies ou de films en un seul (relion_star_handler "
                   "--combine), en retirant les particules présentes deux fois.",
        "when": [
            "Plusieurs sessions de collecte traitées séparément.",
            "Réunir les particules de plusieurs classes 2D ou 3D.",
        ],
        "avoid": ["Jeux de types différents (particules et micrographies) : impossible."],
        "inputs": "Deux à quatre sorties du même type.",
        "tips": ["Les groupes optiques de chaque jeu sont conservés."],
        "next": ["relion_class2d", "relion_refine3d", "relion_subset"],
    },
    "relion_subset": {
        "purpose": "relion_star_handler sur un jeu de particules ou de micrographies : retirer les particules en double "
                   "(plus proches qu'une distance après alignement : elles gonflent la FSC), découper en sous-ensembles, "
                   "garder une plage d'une étiquette de métadonnées ou écarter les images aux statistiques aberrantes.",
        "when": [
            "Après une ré-extraction recentrée : doublons d'un picking trop dense.",
            "Découper un gros jeu pour des essais rapides.",
        ],
        "avoid": ["Distance de doublons proche du diamètre : de vraies particules voisines seraient retirées."],
        "inputs": "Des particules ou des micrographies.",
        "tips": [
            "Doublons : une distance de 30 à 50 % du diamètre.",
            "Sélection par valeur : n'importe quelle étiquette (rlnCtfMaxResolution, rlnDefocusU...).",
        ],
        "next": ["relion_refine3d", "relion_class2d", "relion_join"],
    },
    "relion_particle_sets": {
        "purpose": "Compare deux jeux de particules (comme le « Particle Sets Tool » de CryoSPARC) : les particules "
                   "présentes dans les deux, celles de A seulement et celles de B seulement. Les particules se reconnaissent "
                   "à leur image ou à leur position sur la micrographie. Deux jeux de micrographies se comparent par leur "
                   "nom, comme avec l'« Exposure Sets Tool ».",
        "when": [
            "Retirer d'un jeu les particules d'un autre (classes « poubelle », particules déjà traitées).",
            "Garder les particules retenues par deux nettoyages indépendants (2D et 3D, deux pickings).",
        ],
        "avoid": ["La correspondance par image entre particules ré-extraites : elles ont d'autres noms d'image ; prenez la position."],
        "inputs": "Deux jeux de particules (RELION ou CryoSPARC), ou deux jeux de micrographies.",
        "tips": [
            "Position : distance d'environ un quart du diamètre ; les décalages de l'alignement sont pris en compte.",
            "Micrographies : par nom complet, ou par nom de fichier quand les dossiers diffèrent (mouvement et CTF).",
            "L'union des deux jeux se fait avec « Join star files ».",
        ],
        "next": ["relion_refine3d", "relion_class2d", "relion_join"],
    },
    "relion_particle_curate": {
        "purpose": "Garde les particules dont les statistiques sont dans des limites, comme « Subset Particles by Statistic » "
                   "de CryoSPARC, mais en interactif : probabilité de la meilleure pose (rlnMaxValueProbDistribution), "
                   "échantillons significatifs, contribution à la log-vraisemblance, correction de normalisation, figure de "
                   "mérite du picking, défocus, ajustement CTF, décalage de l'alignement, nombre de particules sur la "
                   "micrographie et toute autre colonne RELION. Le job attend avec l'histogramme de chaque statistique (les "
                   "barres comptent les particules que gardent les autres limites), une carte de densité de deux "
                   "statistiques et des images de particules gardées et écartées. Ou bien il applique d'un coup les limites "
                   "écrites, ou sépare les particules en groupes d'une statistique (mélange de gaussiennes).",
        "when": [
            "Après une classification 2D ou 3D : écarter les particules mal alignées (probabilité de la meilleure pose "
            "basse, beaucoup d'échantillons significatifs) avant le raffinement.",
            "Après un raffinement : retirer les particules à normalisation aberrante ou à faible contribution à la "
            "vraisemblance.",
            "Micrographies surchargées (agrégats) : limite sur le nombre de particules par micrographie.",
            "Séparer en deux populations nettes d'une statistique : mode « Clusters » (deux gaussiennes ou plus).",
        ],
        "avoid": [
            "Des limites serrées sur une seule itération : regardez les images des particules écartées avant de valider.",
            "Comparer des statistiques entre jobs différents : la log-vraisemblance dépend du jeu, de la boîte et du modèle.",
        ],
        "inputs": "Des particules ; celles d'une classification ou d'un raffinement RELION ont le plus de statistiques.",
        "tips": [
            "Glissez sur un histogramme pour une limite (double-clic : aucune) ; « N out » compte les particules qu'elle écarte.",
            "Les barres bleues tiennent compte des autres limites : on voit ce qu'une limite de plus retirerait vraiment.",
            "Les images se renouvellent après chaque changement (« Other particles » pour un autre tirage).",
            "Agents et scripts : mode « thresholds » avec, par ligne, « statistique minimum maximum » (- pour aucune), "
            "par exemple « max_prob 0.1 - ».",
            "Au-delà d'un million de particules, les graphiques montrent un échantillon ; les limites s'appliquent à toutes.",
        ],
        "next": ["relion_refine3d", "relion_class2d", "relion_class3d_global", "relion_particle_sets"],
    },
    "relion_restack": {
        "purpose": "Réécrit les images d'un jeu de particules dans de nouvelles piles (relion_stack_create), comme « Restack "
                   "Particles » de CryoSPARC : un sous-ensemble gardé après nettoyage ne lit plus que ses propres images, "
                   "dans des fichiers moins nombreux et plus petits. Les particules peuvent être recentrées sur les décalages "
                   "de l'alignement, arrondis au pixel (sans interpolation).",
        "when": [
            "Après le nettoyage 2D/3D d'un grand jeu : les jobs suivants lisent moins de données (disques réseau, cache).",
            "Avant de supprimer une extraction volumineuse dont on ne garde qu'une partie.",
        ],
        "avoid": ["Un jeu encore appelé à changer beaucoup : chaque restack réécrit toutes les images gardées."],
        "inputs": "Des particules (le recentrage demande leurs décalages : classification ou raffinement).",
        "tips": [
            "Une pile par micrographie par défaut, comme l'extraction ; une pile unique s'écrit image par image.",
            "Le recentrage arrondi garde le reste du décalage dans le fichier ; pour un vrai recentrage avec nouvelle "
            "boîte, ré-extrayez (extraction avec « Particles to re-extract »).",
        ],
        "next": ["relion_refine3d", "relion_class2d", "relion_class3d_global"],
    },
    "relion_rebalance": {
        "purpose": "Retire des particules dans les vues trop représentées (orientation préférentielle), comme « Rebalance "
                   "Orientations » de CryoSPARC : les directions de vue sont découpées en cases d'aire à peu près égale et "
                   "chaque case garde au plus le nombre de particules d'un percentile des cases.",
        "when": [
            "Carte étirée dans une direction, FSC directionnelle très anisotrope, quelques vues qui dominent la carte "
            "des directions de vue.",
        ],
        "avoid": ["Un jeu déjà petit : retirer des particules coûte de la résolution ; mieux vaut collecter inclinée."],
        "inputs": "Des particules raffinées (avec leurs poses).",
        "tips": [
            "Cases de 10° et percentile 80 % pour commencer ; comparez les cartes des directions de vue avant et après.",
            "Raffinez ensuite les particules gardées et comparez l'anisotropie (FSC directionnelle).",
        ],
        "next": ["relion_refine3d"],
    },
    "relion_downsample": {
        "purpose": "Réduit la taille des images de particules par recadrage de Fourier (comme « Downsample Particles » de "
                   "CryoSPARC) : 2D et 3D plus rapides à un pixel plus gros. Écrit de nouvelles piles et un fichier de "
                   "particules RELION avec la nouvelle boîte et le nouveau pixel ; poses et décalages (en Å) sont gardés.",
        "when": [
            "Particules extraites à pleine taille : classification 2D, modèle initial et 3D à pixel réduit.",
        ],
        "avoid": ["Le raffinement final à pixel réduit : la résolution est limitée à deux fois le nouveau pixel (Nyquist)."],
        "inputs": "Des particules (RELION ou CryoSPARC).",
        "tips": [
            "Boîte paire ; nouveau pixel = ancien pixel × ancienne boîte / nouvelle boîte.",
            "Pour revenir à pleine taille, ré-extrayez les particules (extraction avec « Particles to re-extract »).",
        ],
        "next": ["relion_class2d", "relion_initial_model", "relion_class3d_global"],
    },
    "relion_symmetry_expand": {
        "purpose": "Copie chaque particule une fois par pose équivalente par symétrie (relion_particle_symmetry_expand), "
                   "comme la « Symmetry Expansion » de CryoSPARC : chaque copie d'un complexe symétrique devient une "
                   "particule. Expansion hélicoïdale le long de l'axe aussi.",
        "when": [
            "Classification ou raffinement focalisés d'une sous-unité d'un complexe symétrique, en C1.",
            "Sous-unités qui diffèrent d'une copie à l'autre (occupation partielle d'un ligand).",
        ],
        "avoid": ["Raffiner les copies avec la symétrie d'origine : les particules seraient comptées plusieurs fois."],
        "inputs": "Des particules raffinées avec la symétrie à développer.",
        "tips": [
            "Ensuite : soustraction du signal autour d'une sous-unité, puis classification 3D sans alignement ou "
            "raffinement local en C1.",
            "Le nombre de particules est multiplié par l'ordre du groupe (14 pour D7).",
        ],
        "next": ["relion_subtract", "relion_class3d_global", "relion_local_refine"],
    },
    "relion_optics_groups": {
        "purpose": "Répartit films, micrographies ou particules en groupes d'optique (comme les « Exposure Group "
                   "Utilities » de CryoSPARC) : par le décalage de faisceau (image shift) lu dans les fichiers XML d'EPU "
                   "et regroupé, par une partie des noms de fichiers, ou par ordre d'acquisition. Le CTF refinement "
                   "ajuste ensuite le beam tilt et les aberrations de chaque groupe.",
        "when": [
            "Collecte avec image shift (AFIS) : chaque trou d'un motif a son propre beam tilt.",
            "Plusieurs sessions ou grilles dans un même jeu : un groupe par session.",
        ],
        "avoid": ["Des groupes de quelques centaines de particules seulement : les aberrations s'estiment mal."],
        "inputs": "Films, micrographies ou particules RELION ; pour le beam shift, le dossier des XML d'EPU.",
        "tips": [
            "Nombre de groupes = trous par position de platine (9 pour 3 × 3) ; vérifiez les nuages sur le graphe des "
            "beam shifts.",
            "Motif de nom : une expression régulière avec un groupe entre parenthèses, p. ex. GridSquare_(\\d+).",
            "Faites-le tôt (sur les films ou micrographies) : les particules extraites héritent des groupes.",
        ],
        "next": ["relion_ctf_refine", "relion_motioncorr", "relion_extract"],
    },
    "relion_multibody": {
        "purpose": "Raffine séparément des corps rigides d'un complexe flexible, chacun avec son masque, à partir d'un "
                   "raffinement consensus (relion_refine --multibody_masks), puis analyse leurs mouvements "
                   "(relion_flex_analyse) : les mouvements principaux en séries de cartes à jouer dans le visualiseur.",
        "when": ["Complexe fait de domaines rigides qui bougent les uns par rapport aux autres."],
        "avoid": [
            "Corps trop petits (moins de 100-150 kDa) : leur alignement devient instable.",
            "Flexibilité continue sans domaines rigides : DynaMight ou cryoDRGN.",
        ],
        "inputs": "Les particules d'un « 3D auto-refine (RELION) » et deux à six masques doux de corps dessinés sur la "
                  "carte consensus.",
        "tips": [
            "Le job reprend la dernière itération du raffinement aux demi-jeux séparés (RELION refuse l'itération finale, "
            "où ils sont réunis).",
            "Les plus grands corps d'abord ; priors de 10° et 2 Å par défaut.",
            "L'analyse en composantes principales donne la part de variance de chaque mouvement.",
        ],
        "next": ["relion_subset", "relion_refine3d", "composite_map"],
    },
    "relion_dynamight": {
        "purpose": "Apprend les déformations continues des particules à partir d'un modèle de gaussiennes de la carte "
                   "consensus (DynaMight, RELION 5), puis, au choix, les déformations inverses et une rétroprojection "
                   "déformée : une carte consensus améliorée là où la particule bouge.",
        "when": ["Flexibilité continue qui floute la carte consensus."],
        "avoid": [
            "Sans GPU : très lent.",
            "Particules sans poses raffinées : DynaMight part d'un raffinement consensus.",
        ],
        "inputs": "Les particules raffinées et la carte consensus (même boîte et même taille de pixel).",
        "tips": [
            "Nombre de gaussiennes : 1 à 2 par résidu de la structure.",
            "L'exploration de l'espace latent est une fenêtre interactive à lancer à la main (la commande est dans le rapport).",
        ],
        "next": ["relion_postprocess", "locscale", "relion_locres"],
    },
    # ----------------------------------------------------------- Map processing
    "map_fsc": {
        "purpose": "FSC gold-standard entre demi-cartes : sans masque, avec masque et corrigée par randomisation de phase "
                   "(Chen et al. 2013). Écrit la courbe au format XML de l'EMDB.",
        "when": [
            "Vérifier ou recalculer la résolution annoncée.",
            "Après avoir changé de masque.",
            "Obtenir le fichier FSC XML pour le dépôt.",
        ],
        "avoid": [
            "Demi-cartes qui ne sont plus indépendantes (après spIsoNet ou un traitement commun) : la FSC serait surestimée.",
        ],
        "inputs": "Demi-cartes non filtrées et, idéalement, le masque FSC (doux).",
        "tips": [
            "C'est la courbe « corrigée » qu'on rapporte (seuil 0,143).",
            "Un masque trop serré fait remonter la courbe masquée à haute résolution : la correction le compense en partie.",
        ],
        "next": ["directional_fsc", "deposition_package"],
    },
    "directional_fsc": {
        "purpose": "Résolution dans chaque direction (FSC coniques, « 3D FSC ») pour mesurer l'anisotropie causée par "
                   "l'orientation préférentielle des particules, avec le cFAR et, si les particules sont branchées, le "
                   "SCF : les indicateurs de l'« Orientation Diagnostics » de CryoSPARC.",
        "when": [
            "La distribution angulaire de CryoSPARC montre des vues dominantes.",
            "La densité paraît étirée selon un axe, ou les hélices sont nettes dans un plan et floues dans un autre.",
            "Avant de décider d'un spIsoNet ou d'une nouvelle collecte inclinée (tilt).",
        ],
        "avoid": [
            "Très petites particules : chaque cône contient peu de coefficients, le résultat est bruité.",
            "Ce n'est pas une résolution locale (par région) mais par direction.",
        ],
        "inputs": "Demi-cartes non filtrées et masque FSC ; facultatif : les particules du raffinement (leurs orientations).",
        "tips": [
            "Rapport pire/meilleure direction ≤ 1,25 : isotrope ; 1,25–1,6 : modéré ; > 1,6 : anisotropie forte.",
            "cFAR (aire sous la pire FSC conique / aire sous la meilleure, chaque coquille pondérée par sa surface) : "
            "< 0,5 signale une orientation préférentielle (seuil de CryoSPARC) ; une seule mauvaise direction suffit à le faire baisser.",
            "SCF (Baldwin & Lyumkis 2019), calculé sur 10 000 particules au plus : 1 pour des vues uniformes, 0,81 pour des "
            "vues de côté seules, moins avec des vues dominantes ; la carte « Fourier sampling by direction » montre les "
            "directions jamais échantillonnées (cône manquant).",
            "Élévation 90° = axe z de la carte ; directions opposées équivalentes (hémisphère).",
        ],
        "next": ["spisonet", "locscale", "relion_rebalance"],
    },
    "map_tools": {
        "purpose": "Opérations simples sur une carte, comme les « Volume Tools » de CryoSPARC : taille de pixel (en-tête), "
                   "rééchantillonnage (autre taille de pixel par recadrage ou remplissage de Fourier, ou sur la grille "
                   "d'une autre carte), recadrage/agrandissement de boîte, inversion de main, B-factor global, filtre "
                   "passe-bas, multiplication par un masque, normalisation.",
        "when": [
            "La main est fausse (hélices gauches, ModelAngelo ne construit que des fragments) : « Flip handedness ».",
            "Boîte non cubique ou trop grande pour un logiciel : recadrer en boîte cubique.",
            "Carte d'un autre jeu de données ou d'un autre logiciel à comparer : « Resample to pixel size » ou la grille "
            "d'une autre carte (entrée « Grid of »).",
            "Carte filtrée à une résolution donnée pour une figure ou un ajustement grossier.",
        ],
        "avoid": ["Recadrer la carte déposée sans recadrer de même les demi-cartes et le masque.",
                  "Agrandir le pixel au-delà de la moitié de la résolution : les détails au-delà de Nyquist sont perdus."],
        "inputs": "Une carte ; le masque seulement pour « Multiply by mask » ; une carte de référence pour sa grille.",
        "tips": [
            "Les opérations s'enchaînent dans un ordre fixe : pixel → rééchantillonnage → boîte → main → B-factor → filtre "
            "→ masque → normalisation.",
            "« Set pixel size » ne change que l'en-tête (calibration fausse) ; « Resample » change la grille.",
            "B-factor négatif = affûtage, positif = flou.",
        ],
        "next": ["modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "fit_in_map", "chimerax_fitmap"],
    },
    "segment_map": {
        "purpose": "Découpe une carte en régions (domaines, sous-unités), comme Segger dans ChimeraX : bassins versants de "
                   "la densité lissée au-dessus d'un seuil, puis régions réunies en commençant par celles que sépare la "
                   "vallée la moins profonde, jusqu'au nombre demandé. Un masque à bord doux par région, la plus grande "
                   "d'abord : les corps d'un raffinement multi-corps, le masque d'un raffinement local, d'une "
                   "classification focalisée ou d'une soustraction.",
        "when": [
            "Préparer les masques des corps d'un « 3D multi-body refinement » ou d'un raffinement local.",
            "Repérer les domaines d'une carte consensus avant de choisir où focaliser.",
        ],
        "avoid": [
            "Une carte très bruitée ou à basse résolution : les vallées entre domaines disparaissent (lissez davantage).",
            "Des corps trop petits pour le multi-corps (moins de ~100-150 kDa) : RELION ne les aligne pas bien.",
        ],
        "inputs": "Une carte consensus (post-traitée de préférence) ; en option un masque qui limite la région découpée.",
        "tips": [
            "Seuil automatique : celui qui contient la masse attendue si elle est donnée, sinon la méthode d'Otsu.",
            "Lissage 10-15 Å pour des domaines ; plus fin, les domaines se découpent en petits morceaux.",
            "Les masques se recouvrent sur leur bord doux, comme RELION le conseille pour le multi-corps.",
            "Les couleurs du rapport (bleu, orange, vert…) suivent l'ordre des régions et le nom des sorties.",
        ],
        "next": ["relion_multibody", "relion_local_refine", "relion_subtract", "relion_class3d"],
    },
    "create_mask": {
        "purpose": "Crée un masque à bord doux : à partir de la densité (passe-bas + seuil), des atomes de chaînes choisies, "
                   "de la carte simulée à partir de ces atomes (comme molmap dans Chimera/ChimeraX), ou de la densité "
                   "proche de ces atomes. Dilatation et bord doux en Å, comme dans CryoSPARC ; combinaison avec un autre "
                   "masque (union, soustraction, intersection).",
        "when": [
            "Masque de raffinement local d'une sous-partie d'un gros complexe (chaînes choisies + densité voisine).",
            "Masque lisse d'un domaine à partir du modèle (« model map ») : la recette molmap + seuil + bord doux de "
            "Chimera, sans quitter CryoPlug ; la carte simulée est aussi donnée (sortie « model_map »).",
            "Masque FSC, LocScale ou spIsoNet quand celui de CryoSPARC manque ou ne convient pas.",
            "Soustraction de signal : masque du complexe moins la région à garder (« subtract »).",
            "Masque à fournir à l'EMDB avec la carte déposée.",
        ],
        "avoid": [
            "Masque trop serré (dilatation + bord < 6 Å) pour la FSC : il gonfle la résolution.",
            "Seuil automatique sans masse attendue sur une carte très bruitée : vérifiez la valeur et les coupes.",
        ],
        "inputs": "Une carte (elle fixe la boîte et le pixel) ; un modèle ajusté pour les masques par chaînes ; un "
                  "second masque pour les combinaisons.",
        "tips": [
            "Raffinement local : densité proche du modèle (6–8 Å), dilatation 3–5 Å, bord 8–12 Å.",
            "« Model map » : une gaussienne par atome (σ = résolution / (π√2), hauteur = numéro atomique, comme molmap) ; "
            "10–15 Å et le contour à 95 % (celui de ChimeraX) donnent un masque lisse de domaine, à dilater de 2–3 Å.",
            "FSC / LocScale : densité filtrée à 15 Å, dilatation 3 Å, bord 6 Å.",
            "Donnez la masse attendue (kDa) pour un seuil automatique fiable ; sinon la masse des atomes choisis est utilisée.",
            "Les coupes du rapport montrent le masque (orange) sur la carte : vérifiez qu'il ne coupe pas de densité.",
            "Le chemin du fichier est donné pour l'importer dans CryoSPARC (Import 3D Volumes, type mask).",
        ],
        "next": ["map_fsc", "locscale", "spisonet", "local_resolution", "relion_local_refine", "relion_subtract"],
    },
    "draw_mask": {
        "purpose": "Masque dessiné à la main sur une carte dans le visualiseur 3D, comme dans Chimera : sphères, boîtes et "
                   "cylindres placés à la souris, région de densité connexe cliquée au niveau de contour, zone autour de "
                   "chaînes, carte simulée à partir de chaînes d'un modèle (molmap), densité au-dessus d'un niveau, ajoutés, "
                   "effacés ou intersectés ; puis dilatation et bord doux. "
                   "Le job calcule le masque sur la grille complète de la carte, avec son centre de masse et la boîte "
                   "pour recentrer les particules.",
        "when": [
            "Masque de raffinement local ou de soustraction d'une région qu'aucun modèle ne décrit encore.",
            "Retoucher un masque automatique : effacer une densité parasite, ajouter un domaine flexible.",
            "Masque de classification focalisée autour d'un site (ligand, domaine mobile).",
        ],
        "avoid": [
            "Masque FSC global : « Create mask » (densité filtrée + seuil) est plus reproductible.",
            "Bord trop dur pour un raffinement : gardez dilatation + bord doux ≥ 6-8 Å.",
        ],
        "inputs": "La carte sur laquelle dessiner (elle fixe la boîte et le pixel : prenez la carte consensus pour les "
                  "jobs RELION) ; un modèle ajusté pour les zones autour de chaînes et les cartes simulées (molmap).",
        "tips": [
            "Visualiseur 3D → bouton « Mask » : choisissez un outil, cliquez sur la carte ; la surface orange est le masque.",
            "« Region » prend toute la densité connexe au niveau de contour (réglez le niveau, ou un passe-bas pour lisser).",
            "« Model map » : la carte simulée de chaînes d'un modèle ouvert, coupée au contour qui englobe 95 % de sa "
            "densité ; ou la commande ChimeraX « molmap #2/A,B 10 » dans la ligne de commande du visualiseur.",
            "« At the centre of the view » : cliquez d'abord un atome pour centrer la vue dessus, puis placez la forme.",
            "Ctrl + Z annule, Suppr retire l'opération sélectionnée ; les curseurs modifient l'opération sélectionnée.",
            "Un job créé dans un workflow attend le dessin : « Draw in 3D » sur ce job, puis « Save as job » le lance.",
        ],
        "next": ["relion_subtract", "relion_local_refine", "relion_class3d", "map_fsc", "locscale"],
    },
    "local_resolution": {
        "purpose": "Carte de résolution locale à partir des deux demi-cartes (phenix.local_resolution) : distribution dans "
                   "la molécule et, avec un modèle, résolution de chaque chaîne et de chaque résidu. Le visualiseur 3D "
                   "colorie une carte avec.",
        "when": [
            "Gros complexe : savoir quelles sous-unités sont bien résolues et lesquelles demandent un raffinement local.",
            "Avant de construire ou d'interpréter des chaînes latérales dans une région périphérique.",
            "Figure de résolution locale pour l'article et l'EMDB.",
            "Après une carte composite (avec ses demi-cartes composites).",
        ],
        "avoid": [
            "Demi-cartes non indépendantes (après spIsoNet, LocScale ou un débruitage commun) : la résolution serait surestimée.",
            "Demi-cartes masquées ou filtrées : donnez les demi-cartes brutes.",
        ],
        "inputs": "Demi-cartes non filtrées ; la carte à colorier (affûtée), le masque FSC et le modèle sont facultatifs.",
        "tips": [
            "Le tableau par chaîne classe les sous-unités de la mieux à la moins bien résolue.",
            "model_local_resolution.cif porte la résolution locale dans la colonne B-factor (coloriage dans ChimeraX, Coot, PyMOL).",
            "Dans le visualiseur : menu Colour de la carte → Local resolution ; l'échelle va du 5e au 95e centile.",
            "Une carte de résolution locale importée de CryoSPARC s'utilise de la même façon.",
        ],
        "next": ["create_mask", "locscale", "mapmodel_validation"],
    },
    "composite_map": {
        "purpose": "Assemble les cartes des raffinements locaux (focalisés) en une carte composite "
                   "(phenix.combine_focused_maps) : chaque partie du modèle prend la carte où il s'ajuste le mieux. Les "
                   "demi-cartes de chaque carte sont combinées de la même façon quand elles existent.",
        "when": [
            "Gros complexe résolu par plusieurs raffinements locaux dans CryoSPARC (une carte nette par région).",
            "Construire et affiner un modèle unique dans une seule carte au lieu de jongler entre les cartes locales.",
            "Préparer le dépôt : l'EMDB attend la carte composite avec les cartes locales et la carte consensus.",
        ],
        "avoid": [
            "Rapporter la FSC de la carte composite comme une FSC gold-standard : donnez celles des cartes locales.",
            "Cartes dont les boîtes ou les tailles de pixel diffèrent beaucoup : vérifiez-les avant.",
        ],
        "inputs": "Le modèle ajusté dans la carte consensus, la carte consensus (référence) et jusqu'à 8 cartes locales "
                  "(par exemple un import CryoSPARC par raffinement local).",
        "tips": [
            "Les demi-cartes sont retrouvées automatiquement à côté de chaque carte (même job) : il faut les avoir pour toutes.",
            "Les demi-cartes composites permettent ensuite la FSC et la résolution locale de la carte composite.",
            "Un emplacement « Focused map » de plus apparaît dès que le précédent est rempli.",
        ],
        "next": ["local_resolution", "phenix_real_space_refine", "isolde_session"],
    },
    "locscale": {
        "purpose": "Affûtage local (local amplitude scaling) : renforce le contraste là où la carte est moins résolue sans "
                   "sur-affûter les régions bien résolues. Modes model-free (référence EMmerNet), pseudo-modèle, "
                   "model-based et hybride.",
        "when": [
            "Résolution locale très variable : cœur net, périphérie, domaines mobiles ou micelle flous.",
            "La carte affûtée globalement est trop bruitée en périphérie ou trop lissée au cœur.",
            "Une fois un premier modèle disponible : mode model-based (modèle complet) ou hybride (modèle partiel) pour la carte d'interprétation finale.",
        ],
        "avoid": [
            "Demi-cartes déjà filtrées ou affûtées : l'information perdue ne se récupère pas.",
            "Mode model-based avec un modèle incomplet : préférez le mode hybride.",
            "Déposer la carte LocScale à la place de la carte primaire (elle peut être déposée en carte additionnelle).",
        ],
        "inputs": "Demi-cartes non filtrées de CryoSPARC (préférées), masque, et un modèle pour les modes model-based/hybride.",
        "tips": [
            "Renseignez la symétrie pour une référence symétrisée.",
            "Le mode model-free utilise EMmerNet : un GPU est fortement conseillé.",
            "Le mode pseudo-modèle convient aux cartes très bruitées ou de basse résolution.",
        ],
        "next": ["modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "fit_in_map", "chimerax_fitmap",
                 "isolde_session"],
    },
    "emmernet": {
        "purpose": "Carte « améliorée » par le réseau EMmerNet (LocScale 2) avec estimation d'incertitude Monte-Carlo.",
        "when": [
            "Aide à l'interprétation : connectivité de la chaîne, chaînes latérales, régions difficiles.",
            "Comparer avec la carte LocScale ou affûtée pour lever un doute.",
        ],
        "avoid": [
            "Seule preuve d'un détail structural : un réseau peut « halluciner » de la densité.",
            "Validation ou carte primaire du dépôt.",
        ],
        "inputs": "Demi-cartes non filtrées (ou carte complète non affûtée).",
        "tips": ["Regardez la carte de confiance/incertitude produite : les zones peu fiables doivent être interprétées avec prudence."],
        "next": ["modelangelo_build", "isolde_session"],
    },
    "deepemhancer": {
        "purpose": "Post-traitement par apprentissage profond : masquage et affûtage local implicites.",
        "when": [
            "Cartes de résolution modérée (≈ 3,5–6 Å) pour visualiser, faire des figures, ajuster des domaines.",
            "Région flexible ou micelle gênante pour la lecture de la carte.",
        ],
        "avoid": [
            "Construction automatique : ModelAngelo fonctionne moins bien sur des cartes DeepEMhancer (recommandation des auteurs).",
            "Carte primaire du dépôt, validation.",
        ],
        "inputs": "Demi-cartes non filtrées (préférées) ou carte complète.",
        "tips": [
            "tightTarget par défaut ; wideTarget conserve plus de densité autour de la particule ; highRes pour les cartes meilleures que 4 Å.",
        ],
        "next": ["fit_in_map", "chimerax_fitmap", "isolde_session"],
    },
    "emready": {
        "purpose": "Amélioration de carte par apprentissage profond combinant informations locales et non locales.",
        "when": ["Alternative à DeepEMhancer/EMmerNet pour l'interprétation, notamment le tracé de la chaîne principale."],
        "avoid": ["Mêmes limites que les autres réseaux : interprétation seulement, pas de validation ni de dépôt comme carte primaire."],
        "inputs": "La carte (non affûtée ou affûtée), masque optionnel.",
        "tips": ["Un pas (stride) plus petit est plus précis mais plus lent."],
        "next": ["fit_in_map", "chimerax_fitmap", "modelangelo_build"],
    },
    "spisonet": {
        "purpose": "Corrige l'anisotropie due à l'orientation préférentielle : un réseau auto-supervisé apprend, à partir des "
                   "demi-cartes et de la 3D FSC, à restaurer les directions mal échantillonnées.",
        "when": [
            "Anisotropie marquée (rapport > 1,5 dans « Directional resolution ») ou densité visiblement étirée.",
            "Impossible de recollecter avec inclinaison ou de rééquilibrer les vues.",
        ],
        "avoid": [
            "Anisotropie faible : peu de gain, temps de calcul élevé.",
            "Utiliser les demi-cartes corrigées pour la FSC rapportée ou pour Servalcat : elles ne sont plus indépendantes.",
        ],
        "inputs": "Demi-cartes non filtrées et non masquées + masque doux (obligatoire).",
        "tips": [
            "Comptez des dizaines de minutes sur GPU.",
            "Déposez toujours les cartes d'origine ; la carte corrigée peut être déposée en carte additionnelle et mentionnée dans les méthodes.",
            "Jugez le résultat visuellement et par l'ajustement du modèle (Q-score), pas par une nouvelle FSC.",
        ],
        "next": ["modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "isolde_session"],
    },
    "phenix_resolve_cryo_em": {
        "purpose": "Modification de densité au maximum de vraisemblance à partir des demi-cartes (phenix.resolve_cryo_em).",
        "when": [
            "Améliorer l'interprétabilité, souvent aussi la FSC demi-cartes, surtout entre 3 et 5 Å.",
            "Avant une construction automatique à résolution moyenne.",
        ],
        "avoid": ["Comme carte primaire du dépôt (déposez-la en carte additionnelle)."],
        "inputs": "Demi-cartes non filtrées ; la séquence améliore le résultat (sinon donner la teneur en solvant).",
        "tips": ["Augmentez nproc : le calcul est long sur de grandes boîtes."],
        "next": ["modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "phenix_real_space_refine"],
    },
    "phenix_auto_sharpen": {
        "purpose": "Affûtage global optimisé pour maximiser le détail et la connectivité (phenix.auto_sharpen).",
        "when": ["La carte affûtée de CryoSPARC semble sur- ou sous-affûtée.", "Vous partez d'une carte non affûtée d'un autre logiciel."],
        "avoid": ["Résolution locale très hétérogène : préférez LocScale ou l'affûtage local anisotrope."],
        "inputs": "La carte NON affûtée ; les demi-cartes sont optionnelles.",
        "tips": ["Comparez visuellement avec la carte CryoSPARC dans le visualiseur 3D avant de choisir.",
                 "Phenix 2 remplace auto_sharpen par map_sharpening : CryoPlug le lance avec --force, il fonctionne toujours."],
        "next": ["modelangelo_build", "fit_in_map", "chimerax_fitmap"],
    },
    "phenix_map_sharpening": {
        "purpose": "L'outil d'affûtage de Phenix 2 (phenix.map_sharpening, qui remplace auto_sharpen et local_aniso_sharpen) : "
                   "mise à l'échelle par coquilles à partir des deux demi-cartes (ou d'une carte et d'un modèle ajusté), "
                   "anisotrope et éventuellement locale ; ou affûtage par facteur B d'une seule carte.",
        "when": [
            "Phenix 2.x : la méthode actuelle de Phenix pour affûter une carte avant la construction ou l'affinement.",
            "Carte anisotrope (orientation préférentielle modérée) : la correction anisotrope est faite d'office.",
            "Comparer à LocScale, DeepEMhancer ou au post-traitement RELION sur la même carte.",
        ],
        "avoid": [
            "Une seule carte sans modèle en mode « shell » : impossible, choisissez « b-factor ».",
            "Affûtage local sur une carte pleine taille : très long ; découpez d'abord la région (Map operations).",
        ],
        "inputs": "Les demi-cartes non filtrées (recommandé) ; sinon la carte et un modèle ajusté ; la séquence aide Phenix à "
                  "estimer le volume de la molécule.",
        "tips": [
            "La résolution est facultative en mode « shell », obligatoire en « b-factor ».",
            "Avec Phenix 1.21 ou plus ancien sans map_sharpening : job « Auto-sharpen (Phenix) ».",
        ],
        "next": ["modelangelo_build", "cryoatom_build", "cryoatom_enhanced", "phenix_real_space_refine", "local_resolution"],
    },
    "phenix_local_aniso_sharpen": {
        "purpose": "Affûtage local et anisotrope avec mise à l'échelle dépendant de la résolution, à partir des demi-cartes "
                   "(éventuellement guidé par un modèle).",
        "when": ["Résolution variable avec une légère anisotropie ; alternative Phenix à LocScale."],
        "avoid": ["Anisotropie forte : envisagez spIsoNet."],
        "inputs": "Demi-cartes non filtrées, modèle optionnel.",
        "tips": ["Avec un modèle, le résultat suit mieux la résolution locale réelle.",
                 "Phenix 2 remplace local_aniso_sharpen par map_sharpening : CryoPlug le lance avec --force."],
        "next": ["modelangelo_build", "phenix_real_space_refine"],
    },
    # ----------------------------------------------------------- Model building
    # ----------------------------------------------------------- Heterogeneity
    "cryodrgn_backproject": {
        "purpose": "Vérifie que cryoDRGN lit correctement les particules avant un long entraînement (contrôle du protocole de "
                   "Kinman et al. 2023) : conversion des poses et du CTF, réduction des images, puis rétroprojection des "
                   "10 000 premières particules en une carte à comparer au raffinement consensus.",
        "when": [
            "Avant le premier entraînement sur un nouveau jeu de particules (quelques minutes).",
            "Des volumes cryoDRGN creux, bruités ou sans rapport avec la carte consensus : vérifier les entrées.",
            "Particules venant d'un .star ou d'un job inhabituel (raffinement hétérogène, ab initio).",
        ],
        "avoid": ["Juger la qualité des données sur cette carte : c'est une simple rétroprojection de quelques milliers "
                  "d'images, pas une reconstruction raffinée."],
        "inputs": "Les particules d'un raffinement consensus (Import from CryoSPARC, Import particles, ou une sélection).",
        "tips": [
            "Verdict « inputs read correctly » : la carte ressemble au consensus (même forme, même main) et la FSC entre "
            "demi-lots montre du signal au-delà des premières coquilles.",
            "Densité « negative » : le contraste est l'inverse de ce qu'attend cryoDRGN. Cochez « Do not invert the images » "
            "pour l'entraînement, sinon les volumes sortent creux.",
            "Résolution très mauvaise : poses mal lues. Vérifiez « Poses from » (refinement, heterogeneous refinement, ab "
            "initio) et que les particules viennent bien du raffinement consensus.",
            "L'entraînement cryoDRGN fait ce contrôle tout seul (paramètre « Input check ») : ce job sert à le faire à part, "
            "ou à tester l'autre convention de contraste.",
            "Les particules préparées (images réduites, poses, CTF) sont réutilisées par un entraînement à la même taille.",
        ],
        "next": ["cryodrgn_train"],
    },
    "cryodrgn_train": {
        "purpose": "Reconstruction hétérogène avec cryoDRGN : un réseau de neurones apprend un espace latent des conformations "
                   "et compositions des particules (poses du raffinement consensus). Le job contrôle d'abord les entrées, "
                   "suit les pertes en direct, puis analyse l'espace latent : explorateur interactif, volumes des clusters "
                   "avec alertes « junk », trajectoires, diagnostics et conseils pour la suite.",
        "when": [
            "La carte consensus a des régions floues qui pourraient bouger ou être partiellement occupées.",
            "Chercher des états minoritaires, des sous-unités absentes d'une partie des particules, des mouvements continus.",
            "Nettoyer les particules : repérer les clusters de « junk » et les retirer.",
        ],
        "avoid": [
            "Particules sans poses fiables : faites d'abord un bon raffinement consensus (NU-refine), ou utilisez cryoDRGN-AI "
            "(ab initio), qui apprend les poses.",
            "Particules déjà très filtrées (classifications 2D/3D agressives) : l'hétérogénéité cherchée a pu disparaître.",
            "Lire les volumes cryoDRGN comme des cartes haute résolution : ils montrent les états, ils ne servent pas à "
            "affiner un modèle fin.",
        ],
        "inputs": "Les particules d'un raffinement consensus (Import from CryoSPARC les importe avec les cartes), ou une "
                  "sélection faite dans un espace latent précédent.",
        "tips": [
            "Protocole en deux tours (Kinman et al. 2023) : 1) 128 px, petit réseau (256 × 3), z = 8, 50 époques, sur toutes "
            "les particules, pour repérer le junk ; 2) 256 px, grand réseau (1024 × 3), sur les particules nettoyées. "
            "« Network size » en mode auto suit ce protocole (grand réseau pour une sélection d'un espace latent précédent).",
            "Courbes de perte en direct : la perte de reconstruction baisse puis se stabilise. La carte du job affiche le "
            "temps par époque et le temps restant.",
            "Galerie des volumes : les clusters marqués weak, noisy ou blurry sont souvent du junk. Regardez-les en 3D "
            "(double-clic) avant de les retirer.",
            "Tableau « Is the latent space structural? » : si le défocus ou l'orientation sont fortement codés (R² > 0,3), "
            "une partie de l'espace latent reflète les images et non la structure.",
            "Explorateur : « Colour by » (densité, ‖z‖, défocus, orientation…), lasso pour une région, clic sur une particule "
            "pour son volume, « Particle images » pour voir les images d'un cluster.",
            "Le chargement paresseux (--lazy) s'active seul si les images ne tiennent pas en mémoire. Plusieurs GPU "
            "(--multigpu) : utile à 256 px avec le grand réseau.",
            "CUDA out of memory : baissez le batch size. Erreur d'assertion pendant l'entraînement : décochez « Mixed precision ».",
        ],
        "next": ["cryodrgn_convergence", "select_particles", "cryodrgn_continue", "cryodrgn_analyze", "cryodrgn_landscape",
                 "cryodrgn_trajectory", "series_analysis"],
    },
    "cryodrgn_abinit": {
        "purpose": "Reconstruction hétérogène ab initio avec cryoDRGN-AI (cryodrgn abinit, Levy et al. 2025) : la pose et les "
                   "coordonnées latentes de chaque particule sont apprises ensemble, à partir des seules images et de leur "
                   "CTF, sans raffinement consensus. CryoPlug ajoute le contrôle des entrées, le suivi par phase avec temps "
                   "restant, la stabilité des poses, la comparaison avec les poses consensus (main, repère, écart par "
                   "particule), une rétroprojection de validation, les particules réécrites avec leurs poses apprises et les "
                   "outils d'espace latent des entraînements cryoDRGN.",
        "when": [
            "Le consensus est flou sur une partie de l'objet (grand mouvement, composition variable) : ses poses sont alignées "
            "sur la partie dominante et faussent un entraînement cryoDRGN classique.",
            "Travailler tôt, sur un jeu peu filtré (juste après une classification 2D), pour ne rien jeter trop vite : "
            "cryoDRGN-AI tolère le junk et le regroupe dans l'espace latent.",
            "Mélanges, états rares : l'article a trouvé un nouvel état de la V-ATPase et un supercomplexe rare de l'ankyrine-1.",
            "Contre-expertise d'un entraînement cryoDRGN : deux estimations indépendantes des poses ; si le même paysage "
            "apparaît, l'hétérogénéité n'est pas un artefact d'alignement.",
        ],
        "avoid": [
            "Chercher la meilleure résolution : cryoDRGN-AI découvre, trie et donne des poses ; la carte finale de chaque état "
            "se fait dans CryoSPARC.",
            "Très petites particules (moins de ~250 kDa, le plus petit complexe de l'article) : la recherche de poses devient "
            "fragile.",
            "Particules symétriques dont on veut exploiter la symétrie : cryoDRGN-AI reconstruit en C1.",
            "cryoDRGN avant 4.2 : la commande abinit n'existe pas (et eval_vol ne lit ces modèles qu'à partir de 4.3).",
        ],
        "inputs": "Des particules avec leur CTF : Import from CryoSPARC (ou Import particles) d'un job Select 2D, d'une "
                  "extraction ou d'un raffinement. Si elles portent des poses consensus, elles sont comparées aux poses "
                  "apprises. Sans poses consensus, une carte de référence facultative (même molécule) fixe le repère et la main.",
        "tips": [
            "Réglages de l'article : images à 128 px, z = 8, réseau 3 × 256, 3 époques de recherche de poses puis descente de "
            "gradient (30 époques au total par défaut, 50 à 100 pour de gros jeux).",
            "Les époques de recherche de poses (HPS) sont lentes, les époques SGD environ 20 fois plus rapides : la carte du "
            "job affiche la phase et une estimation du temps restant (« roughly » tant que la vitesse SGD n'est pas mesurée).",
            "Contrôle des entrées : image moyenne des particules (elles doivent être sombres dans les images brutes), plus une "
            "rétroprojection avec les poses consensus si elles existent. « Image contrast = auto » choisit seul "
            "--uninvert-data : un mauvais choix donne des volumes creux.",
            "« Pose changes between checkpoints » : grandes pendant la recherche de poses, puis quelques degrés. Des valeurs "
            "faibles et stables à la fin indiquent des poses convergées.",
            "Comparaison avec le consensus : main (un ab initio peut sortir en miroir, c'est alors corrigé), écart médian, "
            "% de particules à moins de 10°. Coloriez l'explorateur par « Pose difference from the consensus » : des "
            "clusters à grand écart sont du junk ou une autre espèce.",
            "« Put the results in the consensus frame » : volumes et poses dans le repère et la main du consensus (ou de la "
            "carte de référence) ; les volumes se superposent à la carte consensus dans le visualiseur.",
            "Sortie « Particles with their learned poses » (.cs ou .star) : s'importe tel quel dans CryoSPARC (Import Particle "
            "Stack, reconstruction sans réalignement), ou sert à un entraînement cryoDRGN classique.",
            "Validation : la FSC de la rétroprojection avec les poses apprises dit si la recherche de poses a réussi.",
            "Second tour (comme dans l'article) : retirez le junk dans l'explorateur, puis relancez cryoDRGN-AI sur la "
            "sélection (les images réduites sont réutilisées).",
            "CUDA out of memory pendant la recherche de poses : « Batch size (pose search) » à 8.",
        ],
        "next": ["cryodrgn_convergence", "select_particles", "cryodrgn_continue", "cryodrgn_analyze", "cryodrgn_landscape",
                 "cryodrgn_trajectory", "cryodrgn_train", "series_analysis"],
    },
    "cryodrgn_continue": {
        "purpose": "Poursuit l'entraînement d'un modèle cryoDRGN depuis son dernier point de sauvegarde (même réseau, mêmes "
                   "particules), puis l'analyse à nouveau.",
        "when": [
            "Le contrôle de convergence répond « not converged » ou « nearly converged » : le protocole passe de 50 à 100 époques.",
            "Les volumes changent encore d'une époque à l'autre.",
        ],
        "avoid": [
            "Changer de taille d'image, de réseau ou de particules : lancez un nouvel entraînement.",
            "Prolonger un entraînement déjà convergé : risque de sur-apprentissage (volumes plus bruités).",
        ],
        "inputs": "L'espace latent d'un entraînement cryoDRGN ou cryoDRGN-AI (ou d'une continuation précédente).",
        "tips": [
            "« Train until epoch » = nombre total d'époques à la fin (0 = le double).",
            "Modèle cryoDRGN-AI : la continuation ajoute des époques de descente de gradient (cryodrgn abinit --load), puis "
            "refait la comparaison avec le consensus, les particules avec leurs poses apprises et l'analyse.",
            "Les époques précédentes restent dans le dossier du premier job : les courbes et le contrôle de convergence "
            "couvrent tout l'entraînement.",
        ],
        "next": ["cryodrgn_convergence", "select_particles", "cryodrgn_landscape"],
    },
    "cryodrgn_analyze": {
        "purpose": "Analyse à nouveau un modèle cryoDRGN entraîné : autre époque, plus de clusters, trajectoires plus longues, "
                   "main inversée… Même explorateur, mêmes volumes et diagnostics que l'entraînement.",
        "when": [
            "Échantillonner plus finement l'espace latent (50 à 100 volumes) pour voir des états rares.",
            "Les dernières époques donnent des volumes bruités (sur-apprentissage) : analysez l'époque indiquée par le "
            "contrôle de convergence.",
        ],
        "avoid": ["Relancer un entraînement complet pour seulement changer le nombre de volumes."],
        "inputs": "L'espace latent d'un entraînement cryoDRGN.",
        "tips": [
            "Epoch = 0 reprend l'époque de l'entrée (la dernière de l'entraînement).",
            "Volume box : volumes plus petits, plus rapides à générer et à visualiser ; la taille de pixel est corrigée. Les "
            "modèles cryoDRGN-AI restent à la taille d'entraînement (cryoDRGN ne les réduit pas).",
            "Modèle cryoDRGN-AI : « Flip handedness » change la main des volumes et des poses suivantes (elle est gardée dans "
            "le repère du modèle).",
        ],
        "next": ["select_particles", "cryodrgn_trajectory", "cryodrgn_landscape", "series_analysis"],
    },
    "cryodrgn_convergence": {
        "purpose": "Dit si un entraînement cryoDRGN a convergé, comme le recommande le protocole de Kinman et al. (2023) : "
                   "courbe de perte, déplacement des particules dans l'espace latent d'une époque à l'autre, stabilité de "
                   "leurs voisins, et évolution des volumes de particules représentatives au fil des époques. Donne l'époque "
                   "à partir de laquelle les résultats sont stables, ou conseille de prolonger.",
        "when": [
            "Après l'entraînement final, avant d'interpréter des détails.",
            "Choisir l'époque à analyser quand les dernières époques semblent sur-apprises.",
        ],
        "avoid": ["Entraînement avec un seul point de sauvegarde (« Checkpoint every » trop grand)."],
        "inputs": "L'espace latent d'un entraînement cryoDRGN (après une continuation, toutes les époques sont comparées).",
        "tips": [
            "Verdict en tête du rapport (converged, nearly converged, not converged) avec la marche à suivre.",
            "Les sorties « Particle … across epochs » se jouent en 3D : un entraînement convergé montre le même volume à "
            "chaque époque.",
            "L'explorateur montre l'espace latent aux époques comparées, aligné sur la dernière ; « Play the epochs » anime "
            "la mise en place des états.",
            "Une perte qui baisse encore lentement n'empêche pas la convergence : ce sont les volumes et l'organisation de "
            "l'espace latent qui décident.",
            "Modèle cryoDRGN-AI : la stabilité des poses entre points de sauvegarde entre dans le verdict, la perte est jugée "
            "sur les époques SGD et l'époque 0 (pré-entraînement) est ignorée.",
        ],
        "next": ["cryodrgn_continue", "cryodrgn_analyze", "select_particles", "cryodrgn_landscape"],
    },
    "cryodrgn_landscape": {
        "purpose": "Cartographie le paysage conformationnel à partir des volumes plutôt que de l'espace latent (cryodrgn "
                   "analyze_landscape) : un grand nombre de volumes générés dans tout l'espace latent sont comparés par ACP "
                   "dans un masque, puis regroupés en états par classification hiérarchique.",
        "when": [
            "Après un entraînement final convergé : définir des états discrets et leurs particules.",
            "L'UMAP est difficile à lire : des particules éloignées dans l'espace latent peuvent avoir le même volume.",
            "Avec un masque sur un domaine mobile : décrire précisément son mouvement.",
        ],
        "avoid": [
            "Premier passage de nettoyage (128 px) : inutilement coûteux.",
            "Particules où le junk n'a pas été retiré : il forme des états à part.",
        ],
        "inputs": "L'espace latent d'un entraînement cryoDRGN ; un masque facultatif (zone à comparer).",
        "tips": [
            "1000 volumes et 10 états (valeurs par défaut de cryoDRGN) ; moins de volumes pour un essai rapide.",
            "La sortie « latent » porte les états à la place des clusters : l'explorateur (Keep…) ou « Select particles » "
            "prennent les particules d'un état, à raffiner dans CryoSPARC.",
            "Trajectoires « Volume PC » : les principales façons dont les volumes diffèrent dans le masque.",
            "Modèle cryoDRGN-AI : cryoDRGN inverserait ses volumes, puis effacerait la densité négative (la molécule) avant "
            "de les comparer ; CryoPlug le lance sur une copie du modèle qui ne les inverse pas, à la taille "
            "d'entraînement, et remet les volumes dans le repère du consensus avec la bonne taille de pixel.",
        ],
        "next": ["select_particles", "series_analysis", "extract_volume", "cryodrgn_trajectory"],
    },
    "cryodrgn_trajectory": {
        "purpose": "Génère les volumes le long d'un chemin de l'espace latent entre des clusters (ou des particules) choisis : "
                   "à travers les particules (chemin soutenu par les données, cryodrgn graph_traversal) ou en ligne droite. "
                   "Un film de la transition à jouer dans le visualiseur.",
        "when": [
            "Visualiser le passage d'un état à un autre repéré dans l'explorateur latent.",
            "Préparer une figure ou une vidéo de mouvement pour l'article.",
        ],
        "avoid": ["Interpréter un chemin en ligne droite qui traverse des régions vides de particules : préférez le chemin "
                  "à travers les particules. Un chemin n'est pas forcément un chemin biologique."],
        "inputs": "L'espace latent d'un entraînement ou d'une analyse cryoDRGN, et les numéros de clusters à relier.",
        "tips": [
            "Dans l'explorateur, cliquez les clusters dans l'ordre du chemin puis « Trajectory… » : le job est préparé.",
            "Particules au lieu de clusters (paramètre avancé) : relier deux particules précises (numéros affichés par "
            "l'explorateur). « Back to the start » ferme la boucle (mouvement cyclique).",
            "Si le graphe des voisins est coupé entre les deux points, la ligne droite est utilisée (avertissement dans le log).",
            "Volume series analysis sur la trajectoire montre où la densité change.",
        ],
        "next": ["series_analysis", "extract_volume"],
    },
    "cryodrgn_volumes": {
        "purpose": "Génère le volume de particules choisies (à partir de leurs coordonnées latentes), ou d'une région dessinée "
                   "au lasso dans l'explorateur : le volume au centre de la région, ou plusieurs volumes répartis dedans.",
        "when": [
            "Une zone de l'espace latent intrigue mais n'a pas de centre de cluster.",
            "Voir à quoi ressemblent les particules d'une région avant de la garder ou de la retirer.",
        ],
        "avoid": ["Générer des centaines de volumes : utilisez « cryoDRGN analysis » (k-means) ou « landscape analysis »."],
        "inputs": "L'espace latent d'un entraînement ou d'une analyse cryoDRGN.",
        "tips": [
            "Depuis l'explorateur : cliquez une particule puis « Volume of particle … », ou dessinez une région au lasso puis "
            "« Volume of the region… ».",
            "Plusieurs volumes dans une région = centres k-means de ses particules (points sur les données, comme cryoDRGN).",
        ],
        "next": ["series_analysis", "extract_volume"],
    },
    "select_particles": {
        "purpose": "Garde ou retire des particules choisies dans un espace latent (cryoDRGN ou 3D variability) : des clusters "
                   "entiers, une région dessinée au lasso, ou les particules aberrantes, loin des autres. Écrit la sélection "
                   "et les particules écartées (.cs pour CryoSPARC ou .star pour RELION, et les indices pour cryoDRGN).",
        "when": [
            "Des clusters ont des volumes de junk (weak, noisy, blurry dans la galerie) : retirez-les puis réentraînez.",
            "Une région de l'espace latent ou un état intéressant : gardez ses particules et raffinez-les dans CryoSPARC.",
            "Des particules aberrantes (‖z‖ très grand) : retirez-les.",
        ],
        "avoid": ["Sélectionner sur un modèle non convergé : les clusters peuvent encore changer."],
        "inputs": "Un espace latent (cryoDRGN ou 3D variability).",
        "tips": [
            "Le plus simple : depuis l'explorateur, « Keep… » / « Remove… » (clusters), « Keep region… » / « Remove region… » "
            "(lasso) ou « Remove outliers… » préparent le job.",
            "Les particules écartées sont aussi en sortie (excluded) : une classification 2D dans CryoSPARC confirme qu'il "
            "s'agit bien de junk.",
            "Un nouvel entraînement cryoDRGN sur la sélection réutilise les images déjà réduites ; en mode auto il prend le "
            "grand réseau (second tour du protocole).",
            "Outliers : ‖z‖ au-delà de la moyenne + 2 écarts-types (« Outlier threshold »).",
            "Dans CryoSPARC : Import Particle Stack avec le fichier .cs produit, puis Homogeneous / NU refinement pour valider "
            "un état.",
            "Espace latent cryoDRGN-AI : la sélection garde les poses apprises (« Poses of the selection ») ou, au choix, les "
            "poses consensus des particules d'origine, pour un entraînement cryoDRGN classique quand elles concordent.",
        ],
        "next": ["cryodrgn_train", "cryodrgn_abinit", "cryodrgn_backproject"],
    },
    "series_analysis": {
        "purpose": "Interprète une série de volumes (frames de 3D variability, clusters ou trajectoire cryoDRGN) : carte de "
                   "variabilité (où la densité change), cartes moyenne et différence, similarité entre frames et, avec un "
                   "modèle, les chaînes qui bougent ou qui apparaissent et disparaissent.",
        "when": [
            "Après cryoDRGN ou la 3DVA : savoir quelles régions varient et si c'est un mouvement ou une occupation partielle.",
            "Gros complexe : identifier les sous-unités flexibles ou absentes d'une partie des particules.",
        ],
        "avoid": ["Séries de volumes non superposés (boîtes ou origines différentes)."],
        "inputs": "Une série de volumes ; le modèle (ajusté dans ces volumes) et un masque sont facultatifs.",
        "tips": [
            "View 3D colore la carte moyenne par la variabilité (bleu = stable, rouge = variable) ; le menu Colour applique la "
            "même coloration à n'importe quelle carte ou série superposée.",
            "Matrice de corrélation : des blocs = des états distincts ; un dégradé le long de la diagonale = un mouvement continu.",
            "Tableau par chaîne : « fades in some frames » = la chaîne perd sa densité dans certains volumes : absente d'une partie "
            "des particules, ou déplacée hors de sa place dans le modèle (regardez la série pour trancher).",
        ],
        "next": ["extract_volume", "select_particles"],
    },
    "extract_volume": {
        "purpose": "Prend un volume d'une série (un cluster cryoDRGN, une frame de 3DVA) comme carte, pour construire ou "
                   "ajuster un modèle de cet état.",
        "when": ["Ajuster le modèle dans un état particulier (Rigid-body fit, ISOLDE) ou comparer des états."],
        "avoid": ["Affiner finement un modèle dans un volume cryoDRGN basse résolution : raffinez plutôt les particules de cet état dans CryoSPARC."],
        "inputs": "Une série de volumes et le numéro du volume (comme dans le visualiseur).",
        "tips": [
            "Depuis l'explorateur latent : un cluster sélectionné → « Extract map… » prépare ce job avec le bon numéro.",
            "Indiquez la résolution dans les jobs suivants : elle n'est pas connue pour ces volumes.",
        ],
        "next": ["fit_in_map", "chimerax_fitmap", "isolde_session", "mapmodel_validation"],
    },
    "modelangelo_build": {
        "purpose": "Construction automatique de novo (réseaux de graphes) de protéines, ARN et ADN, guidée par la séquence. "
                   "Sans séquence : construction « build_no_seq » et profils HMM pour identifier les chaînes.",
        "when": [
            "Résolution meilleure qu'environ 4 Å (idéalement < 3,5 Å) et séquence connue.",
            "Grands complexes : construit des milliers de résidus en quelques dizaines de minutes.",
            "Sans séquence : sous-unités inconnues (puis « Identify chains »).",
        ],
        "avoid": [
            "Résolution > 4,5 Å : préférez le docking de modèles prédits (AlphaFold, Boltz-2).",
            "Carte DeepEMhancer, carte non cubique (risque de décalage : recadrez en boîte cubique), main inversée (hélices fragmentées).",
        ],
        "inputs": "La meilleure carte affûtée (CryoSPARC, LocScale ou modification de densité) et la séquence.",
        "tips": [
            "Une entrée FASTA par entité, sans répétition des copies.",
            "La sortie « raw » montre tout ce qui a été construit, y compris les régions mal attribuées.",
            "Comparez avec CryoAtom2 et gardez le meilleur selon le Q-score et la complétude.",
        ],
        "next": ["phenix_real_space_refine", "isolde_session", "mapmodel_validation", "modelangelo_hmm_search"],
    },
    "modelangelo_hmm_search": {
        "purpose": "Identifie les protéines (ou ARN/ADN) présentes dans la carte : les profils HMM d'un ModelAngelo sans "
                   "séquence sont recherchés dans une base de séquences (HMMER).",
        "when": [
            "Sous-unité inconnue, contaminant, partenaire co-purifié, densité inattendue.",
            "Échantillon natif (purification endogène) dont la composition exacte est incertaine.",
        ],
        "avoid": [
            "Modèle construit AVEC séquence : il n'y a pas de profils HMM.",
            "Base inutilement énorme (UniProt complet) : plus lent et plus de faux positifs ; préférez le protéome de l'organisme.",
        ],
        "inputs": "Le modèle d'un « ModelAngelo build » lancé sans séquence + un FASTA (ex. protéome de référence UniProt décompressé).",
        "tips": [
            "Les hits sous le seuil d'E-value (1e-5 par défaut) deviennent une sortie « sequence ».",
            "Reconstruisez ensuite avec ces séquences (ModelAngelo ou CryoAtom2) et vérifiez l'accord de séquence dans la carte.",
        ],
        "next": ["cryoatom_build", "cryoatom_enhanced", "modelangelo_build"],
    },
    "cryoatom_build": {
        "purpose": "Construction automatique (CryoAtom2 : attention locale et encodage de position 3D) de protéines, ARN/ADN et "
                   "complexes, avec identification de séquences intégrée et construction locale par masque.",
        "when": [
            "Comme ModelAngelo ; souvent plus complet à résolution moyenne.",
            "Complexes protéine–acide nucléique.",
            "Identification de chaînes avec une base de séquences (protéines et acides nucléiques).",
            "Reconstruire une seule région de la carte (masque) ou identifier une chaîne tracée à la main (backbone Cα/P).",
        ],
        "avoid": [
            "GPU de moins de ~14 Go.",
            "Résolution > 4,5 Å, main inversée (mêmes limites que ModelAngelo).",
        ],
        "inputs": "Carte affûtée ou LocScale, séquence(s) ; base de séquences pour l'identification ; masque/backbone optionnels.",
        "tips": [
            "Itérez : identification → reconstruction avec la sortie « Identified sequences », jusqu'à ce qu'aucune nouvelle séquence n'apparaisse.",
            "Un premier lancement télécharge les poids ESM et RNA-FM (~3 Go).",
            "Comparez avec ModelAngelo (Q-score, résidus construits).",
        ],
        "next": ["phenix_real_space_refine", "isolde_session", "mapmodel_validation"],
    },
    "cryoatom_enhanced": {
        "purpose": "CryoAtom2 lancé plusieurs fois, avec plusieurs réglages (et éventuellement d'autres cartes), chaque modèle "
                   "noté contre la carte expérimentale ; le meilleur est gardé, ses trous comblés par les morceaux bien "
                   "ajustés des autres essais, les chaînes identifiées et reconstruites jusqu'à convergence, la main vérifiée. "
                   "En option, la carte est améliorée avec le modèle (LocScale…) puis reconstruite, tant que le score monte.",
        "when": [
            "Carte difficile (résolution locale basse, régions flexibles) : CryoAtom2 construit différemment à chaque "
            "lancement, et ses auteurs conseillent d'en lancer plusieurs et de garder le meilleur.",
            "Vous ne savez pas quels réglages conviennent : « auto » essaie les réglages standard et, selon la résolution, "
            "« more residues » (seuils Cα/P abaissés) ou « low resolution » (4 tours).",
            "Comparer la construction dans plusieurs cartes (affûtées autrement, LocScale, EMReady…) en les notant toutes "
            "contre la même carte expérimentale.",
            "Composition inconnue : avec une base de séquences, identification puis reconstruction automatiques.",
            "Carte hétérogène (régions bien et mal résolues) : la boucle carte–modèle (« Improve the map and build again », "
            "LocScale hybride avec les demi-cartes) affûte localement avec les parties fiables du modèle, puis reconstruit.",
        ],
        "avoid": [
            "Un seul essai rapide suffit (carte nette à haute résolution) : « CryoAtom2 build » prend N fois moins de GPU.",
            "Carte d'un réseau de neurones utilisée comme carte de référence : les modèles seraient notés contre une densité "
            "déjà interprétée. Branchez la carte expérimentale en « Map » et les autres en « Other map ».",
        ],
        "inputs": "Carte expérimentale (référence de la notation), séquence(s), masque ; demi-cartes non filtrées pour améliorer "
                  "la carte ; cartes alternatives et bases de séquences optionnelles.",
        "tips": [
            "Le tableau « Runs compared » donne, pour chaque essai, les résidus construits, ajustés (Q-score par résidu ≥ Q "
            "attendu − 0,2), dupliqués, et le score : résidus ajustés, une densité tracée deux fois comptée une seule fois.",
            "Comptez « Runs » × réglages × durée d'un CryoAtom2 (≈ 30 min pour 5 000 résidus sur une A100) ; « Maximum runs » "
            "plafonne le premier tour.",
            "Si le job signale une main probablement inversée, retournez la carte (et les demi-cartes) avant d'aller plus loin : "
            "la carte miroir et son modèle sont en sortie.",
            "Boucle carte–modèle : seuls les résidus ajustés à la densité, avec un B uniforme, sont donnés à LocScale (qui "
            "affine leurs B avec REFMAC5 : CCP4 requis) ; LocScale hybride complète le reste par des pseudo-atomes. Le cycle "
            "s'arrête dès qu'il n'améliore plus le score contre la carte expérimentale : la carte améliorée ne sert jamais "
            "de référence, ce qui évite qu'un modèle se « confirme » lui-même.",
            "Les méthodes sans modèle (EMmerNet, LocScale model-free, DeepEMhancer, EMReady2, modification de densité Phenix) "
            "donnent une carte de plus à essayer, une seule fois ; cochez-en plusieurs pour les comparer.",
            "Affinez ensuite le meilleur modèle (real-space refine, ISOLDE) : les modèles bruts gardent des erreurs locales. "
            "La carte améliorée (sortie « map_improved ») aide à interpréter ; déposez la carte expérimentale comme carte "
            "principale.",
        ],
        "next": ["phenix_real_space_refine", "isolde_session", "mapmodel_validation", "checkmysequence"],
    },
    "colabfold_predict": {
        "purpose": "Prédiction AlphaFold2 locale (ColabFold) pour obtenir des modèles de départ.",
        "when": [
            "Résolution insuffisante pour une construction de novo (≈ 4–8 Å).",
            "Modèle de départ d'un domaine ou d'une sous-unité, test d'une hypothèse d'assemblage.",
        ],
        "avoid": [
            "Régions désordonnées (pLDDT bas) ou conformation différente de celle capturée dans la carte.",
            "Complexes avec ligands ou acides nucléiques : préférez Boltz-2.",
        ],
        "inputs": "Séquence(s) protéiques ; « Predict as a complex » pour prédire l'assemblage.",
        "tips": ["Passez ensuite par « Process predicted model » puis un ajustement dans la carte."],
        "next": ["phenix_process_predicted_model", "fit_in_map", "chimerax_fitmap"],
    },
    "boltz_predict": {
        "purpose": "Prédiction de structure de complexes entiers (protéines, ARN/ADN, ligands par code CCD ou SMILES) avec Boltz-2.",
        "when": [
            "Complexe contenant des acides nucléiques, des cofacteurs ou un ligand/médicament.",
            "Modèle de départ pour un docking à 4–8 Å.",
            "Proposer une pose de ligand avant de l'ajuster dans la densité.",
        ],
        "avoid": [
            "Séquences confidentielles avec le serveur MSA activé (les séquences partent vers api.colabfold.com) : désactivez-le.",
            "Considérer la pose prédite d'un ligand comme validée sans densité qui la soutient.",
        ],
        "inputs": "Séquences (une par entité) ; nombre de copies ; ligands (CCD:ATP, 2xCCD:MG, SMILES:…).",
        "tips": [
            "ipTM > 0,8 : interface prédite avec confiance ; < 0,6 : à prendre avec précaution.",
            "Plusieurs échantillons (samples) donnent une idée de la variabilité.",
        ],
        "next": ["phenix_process_predicted_model", "fit_in_map", "chimerax_fitmap", "phenix_dock_in_map"],
    },
    "phenix_process_predicted_model": {
        "purpose": "Prépare un modèle prédit : retire les résidus de faible confiance (pLDDT), convertit le pLDDT en B-factors, "
                   "découpe éventuellement en domaines compacts.",
        "when": ["Toujours avant d'ajuster ou d'affiner un modèle AlphaFold/ColabFold/Boltz.", "Domaines mobiles : découpe pour les ajuster séparément."],
        "avoid": ["Modèle expérimental : ses B-factors ne sont pas des pLDDT."],
        "inputs": "Le modèle prédit.",
        "tips": ["pLDDT minimal 70 par défaut ; baissez-le si vous perdez des régions importantes bien définies dans la carte."],
        "next": ["fit_in_map", "chimerax_fitmap", "phenix_dock_in_map"],
    },
    "phenix_dock_in_map": {
        "purpose": "Recherche globale de la position d'un modèle (ou d'un domaine) dans la carte (phenix.dock_in_map).",
        "when": ["Position inconnue, plusieurs copies possibles, carte de résolution moyenne à basse."],
        "avoid": ["Modèle déjà presque en place : un ajustement local (ChimeraX fitmap) suffit et va plus vite."],
        "inputs": "Modèle (traité s'il est prédit) et carte.",
        "tips": ["Dockez domaine par domaine si l'assemblage diffère de la prédiction, puis fusionnez."],
        "next": ["phenix_real_space_refine", "merge_models"],
    },
    "fit_in_map": {
        "purpose": "Placement de corps rigide d'un modèle, d'une carte ou d'un masque dans une carte, intégré (sans ChimeraX), avec "
                   "l'algorithme de « fitmap » de ChimeraX : à partir de la position réglée à la main dans le visualiseur 3D "
                   "(Move), optimisation locale de la valeur moyenne de la carte aux atomes, ou de l'overlap / corrélation de la "
                   "carte simulée du modèle à une résolution, ou recherche depuis des placements aléatoires.",
        "when": [
            "Placer un modèle prédit (AlphaFold, ColabFold, Boltz) ou un domaine dans la densité avant l'affinement.",
            "Superposer une carte de raffinement local, un masque ou une autre carte sur la carte consensus (sortie sur sa grille).",
            "Écrire la position d'un objet déplacé à la souris dans le visualiseur (« Save as job » de Move & fit).",
        ],
        "avoid": [
            "Changement de conformation important : il faut de la flexibilité (ISOLDE, real-space refine avec morphing).",
            "Recherche globale d'un petit domaine dans une très grande carte : partez d'une position approchée (à la main) "
            "ou limitez le rayon de recherche.",
        ],
        "inputs": "Le modèle (ou la carte / le masque) à placer, et la carte cible ; la position du visualiseur est remplie par "
                  "« Save as job » dans Move & fit.",
        "tips": [
            "Résolution 0 : ajustement des atomes dans la densité (défaut de ChimeraX) ; 6-10 Å avec « correlation » pour un "
            "modèle encore loin de sa place ou une carte à basse résolution.",
            "Modèle à plus de 3-4 Å de sa place : une résolution de 6-8 Å (la carte simulée porte plus loin) ; « Then fit the "
            "atoms » donne ensuite la position exacte.",
            "Recherche : 50-100 placements aléatoires ; « Search radius » pour rester près de la position donnée.",
            "« Chains fitted » : le fit ne regarde que ces chaînes (un domaine, une sous-unité), mais tout le modèle est déplacé.",
            "Dans le visualiseur : commande « fitmap #2 inMap #1 resolution 8 », ou le bouton Fit de Move & fit.",
        ],
        "next": ["phenix_real_space_refine", "servalcat_refine", "isolde_session", "mapmodel_validation", "merge_models"],
    },
    "chimerax_fitmap": {
        "purpose": "Ajustement de corps rigide par corrélation avec une carte simulée à la résolution de la carte (ChimeraX fitmap).",
        "when": [
            "Affiner la position d'un modèle déjà placé (optimisation locale).",
            "Recherche globale d'un domaine (paramètre « Global search placements » > 0).",
        ],
        "avoid": ["Changement de conformation important : il faut une flexibilité (ISOLDE, real-space refine avec morphing)."],
        "inputs": "Modèle et carte (affûtée ou filtrée).",
        "tips": ["200 placements pour une recherche globale ; « Search radius » pour rester près de la position de départ."],
        "next": ["phenix_real_space_refine", "isolde_session", "merge_models"],
    },
    "phenix_elbow": {
        "purpose": "Génère les restreintes géométriques (CIF) et des coordonnées d'un ligand à partir de son code CCD ou d'un SMILES (eLBOW).",
        "when": [
            "Ligand, cofacteur ou médicament absent de la bibliothèque de restreintes de l'outil d'affinement.",
            "Nouveau composé décrit seulement par un SMILES.",
        ],
        "avoid": ["Ions, eaux, acides aminés et nucléotides standards : déjà connus."],
        "inputs": "Code CCD (ATP, NAG…) ou SMILES + nom de résidu.",
        "tips": [
            "Utilisez le code CCD officiel quand le ligand existe : le dépôt est plus simple.",
            "Branchez la sortie « restraints » sur Real-space refinement ou Servalcat ; fusionnez les coordonnées avec « Merge models » si besoin.",
        ],
        "next": ["phenix_real_space_refine", "servalcat_refine", "merge_models"],
    },
    # -------------------------------------------------------------- Interactive
    "isolde_session": {
        "purpose": "Reconstruction interactive par dynamique moléculaire guidée par la carte (ISOLDE dans ChimeraX).",
        "when": [
            "Corriger un modèle automatique ou docké : registre, rotamères, boucles, liaisons peptidiques cis/trans.",
            "Résolution 2,5–4,5 Å, là où la physique aide le plus.",
            "Éliminer les outliers Ramachandran/rotamères et les clashs de façon interactive.",
        ],
        "avoid": ["Serveur sans affichage graphique : utilisez le paquet de session et votre poste, puis renvoyez le modèle."],
        "inputs": "Modèle + carte principale (affûtée ou LocScale) + seconde carte optionnelle (non affûtée, modification de densité).",
        "tips": [
            "À basse résolution, utilisez des restreintes de distance adaptatives sur un modèle de référence.",
            "Sauvegardez dans le dossier du job (save isolde_model.cif models #1) puis « Finish » : les jobs en aval démarrent.",
        ],
        "next": ["phenix_real_space_refine", "servalcat_refine"],
    },
    "coot_session": {
        "purpose": "Construction et correction manuelles classiques dans Coot.",
        "when": [
            "Construire des boucles ou des extrémités manquantes, placer un ligand, des glycanes, des eaux.",
            "Parcourir les listes de problèmes MolProbity (script molprobity_coot.py).",
        ],
        "avoid": ["Corrections globales d'un grand modèle : ISOLDE est souvent plus efficace."],
        "inputs": "Modèle + carte (+ seconde carte).",
        "tips": ["Sauvegardez les coordonnées dans le dossier du job, puis « Finish »."],
        "next": ["phenix_real_space_refine"],
    },
    # --------------------------------------------------------------- Refinement
    "phenix_real_space_refine": {
        "purpose": "Affinement en espace réel avec restreintes géométriques, de structure secondaire, Ramachandran et rotamères, "
                   "morphing, recuit simulé et B-factors (phenix.real_space_refine).",
        "when": [
            "Après chaque étape de construction (automatique, docking, ISOLDE, Coot).",
            "Affinement final court : stratégie minimization_global+adp.",
        ],
        "avoid": [
            "Restreintes Ramachandran si vous rapportez ensuite les statistiques Ramachandran comme validation (elles perdent leur valeur).",
            "Carte fortement modifiée par un réseau comme cible finale : préférez la carte affûtée/primaire.",
        ],
        "inputs": "Modèle + carte (en général la carte affûtée déposée) + restreintes de ligands éventuelles.",
        "tips": [
            "5 macro-cycles par défaut ; morphing/recuit utiles juste après un docking.",
            "N/Q/H flips s'ajoutent à la stratégie (run=…+nqh_flips) : ce n'est pas un paramètre à part pour Phenix.",
            "Si Phenix refuse le modèle (« Map and model are not aligned », symétrie différente de la carte), CryoPlug "
            "relance avec l'option que Phenix propose, avec un avertissement dans le log.",
            "Les statistiques finales (MolProbity, CC_mask) sont lues dans la sortie et affichées.",
        ],
        "next": ["mapmodel_validation", "phenix_validation_cryoem", "isolde_session", "predeposition_check"],
    },
    "servalcat_refine": {
        "purpose": "Affinement contre les demi-cartes non affûtées avec pondération statistique, B-factors atomiques et cartes "
                   "Fo-Fc (Servalcat, sans REFMAC).",
        "when": [
            "Résolution élevée (< 3 Å) et demi-cartes disponibles.",
            "Besoin de cartes de différence Fo-Fc pour repérer ligands, eaux, erreurs de modèle.",
            "Complément ou alternative à Phenix pour l'affinement final.",
        ],
        "avoid": [
            "Demi-cartes non indépendantes (après spIsoNet).",
            "Avec une seule carte : possible, mais on perd l'intérêt principal.",
        ],
        "inputs": "Modèle + demi-cartes non filtrées (+ masque pour Fo-Fc, restreintes de ligands).",
        "tips": [
            "Avec symétrie : modèle de l'unité asymétrique + point group (pg).",
            "Jelly-body à basse résolution ; examinez la carte Fo-Fc normalisée (pics à ±4σ).",
        ],
        "next": ["mapmodel_validation", "coot_session", "phenix_douse"],
    },
    "phenix_douse": {
        "purpose": "Place des molécules d'eau ordonnées dans la carte autour du modèle (phenix.douse).",
        "when": ["Cartes meilleures que ≈ 2,5–3 Å, en fin d'affinement."],
        "avoid": ["Résolution > 3 Å : les eaux ne seraient pas justifiées par la densité."],
        "inputs": "Modèle affiné + carte.",
        "tips": ["Inspectez les eaux ajoutées puis réaffinez le modèle complet."],
        "next": ["phenix_real_space_refine", "servalcat_refine"],
    },
    # --------------------------------------------------------------- Validation
    "phenix_validation_cryoem": {
        "purpose": "Validation complète du modèle : géométrie MolProbity et accord carte–modèle (CC_mask, CC_box…), comme "
                   "dans le rapport wwPDB.",
        "when": ["Modèle final ou quasi final ; comparaison de versions ; avant la soumission."],
        "avoid": ["Comme seul critère : complétez par le Q-score et l'inspection visuelle."],
        "inputs": "Modèle + carte déposée.",
        "tips": ["Voir le guide (chapitre Validation) pour les valeurs cibles selon la résolution."],
        "next": ["predeposition_check", "isolde_session"],
    },
    "phenix_molprobity": {
        "purpose": "Géométrie seule : clashs, Ramachandran, rotamères, déviations Cβ, CaBLAM (MolProbity).",
        "when": ["Contrôle rapide entre deux sessions ISOLDE/Coot ; liste de problèmes à corriger dans Coot."],
        "avoid": ["Ne dit rien de l'accord avec la carte."],
        "inputs": "Le modèle.",
        "tips": ["Le script molprobity_coot.py produit une liste de tâches à ouvrir dans Coot."],
        "next": ["coot_session", "isolde_session"],
    },
    "phenix_emringer": {
        "purpose": "Évalue l'ajustement des chaînes latérales via la densité autour des atomes Cγ (EMRinger).",
        "when": ["Cartes meilleures que ≈ 4,5 Å ; détecter un mauvais registre ou une main inversée."],
        "avoid": ["Basse résolution : le score n'est plus informatif."],
        "inputs": "Modèle + carte.",
        "tips": ["Score > 2 : bon à < 4 Å ; ≈ 1 : médiocre ; proche de 0 : registre ou main douteux."],
        "next": ["isolde_session"],
    },
    "checkmysequence": {
        "purpose": "Vérifie l'attribution de séquence du modèle dans la carte (checkMySequence) : décalages de registre, "
                   "chaînes qui ne correspondent à aucune séquence, différences avec la séquence attendue, ruptures de "
                   "chaîne sans trou de numérotation.",
        "when": [
            "Avant tout dépôt : un registre décalé passe inaperçu dans MolProbity et dans les scores carte–modèle.",
            "Après une construction automatique (ModelAngelo, CryoAtom2) ou une reconstruction manuelle d'une région floue.",
            "Gros complexe : vérifier que chaque chaîne porte la bonne séquence (sous-unités paralogues).",
        ],
        "avoid": [
            "Cartes à plus de 4–4,5 Å : les chaînes latérales ne sont plus assez visibles, les résultats deviennent peu fiables.",
        ],
        "inputs": "Le modèle, la carte dans laquelle il a été construit et toutes les séquences de l'échantillon (FASTA).",
        "tips": [
            "Un décalage signalé indique de combien de résidus déplacer la séquence : corrigez dans Coot ou ISOLDE puis relancez.",
            "« Tracing issues » : des fragments voisins proposent des décalages différents, le tracé de la chaîne est douteux.",
            "Une chaîne « non identifiée » manque souvent du FASTA, ou a été construite hors de la densité.",
            "Il faut HMMER (hmmsearch) dans l'environnement de checkMySequence.",
        ],
        "next": ["isolde_session", "coot_session", "wwpdb_validation"],
    },
    "wwpdb_validation": {
        "purpose": "Rapport de validation officiel du wwPDB (PDF et XML), calculé par le service de validation OneDep : "
                   "le même document que reçoivent les journaux et les relecteurs.",
        "when": [
            "Modèle final, juste avant le dépôt : on découvre les problèmes avant les annotateurs.",
            "Pour joindre le rapport à la soumission d'un article.",
        ],
        "avoid": [
            "Données confidentielles que votre équipe ne veut pas envoyer : le modèle et la carte sont transmis au serveur du wwPDB.",
            "Modèle encore en cours d'affinement : utilisez d'abord les validations locales (plus rapides).",
        ],
        "inputs": "Le modèle final et la carte primaire (celle qui sera déposée).",
        "tips": [
            "Nécessite le client du wwPDB (pip install onedep_api) et un accès internet depuis le serveur CryoPlug.",
            "Les centiles comparent votre modèle à toutes les entrées de la PDB et à celles de résolution voisine : plus haut, c'est mieux.",
            "Une grosse entrée EM peut demander une heure ou plus de calcul côté wwPDB : réglez « Maximum wait » au besoin.",
        ],
        "next": ["deposition_package"],
    },
    "mapmodel_validation": {
        "purpose": "Métriques carte–modèle intégrées : Q-score par atome et par résidu comparé à la valeur attendue, FSC "
                   "carte–modèle, CC_mask/CC_box, inclusion d'atomes au contour suggéré.",
        "when": [
            "Toujours, pour chaque modèle candidat (rapide, sans logiciel externe).",
            "Repérer les résidus mal soutenus par la densité ; comparer ModelAngelo et CryoAtom2.",
        ],
        "avoid": ["Carte modifiée par un réseau : validez contre la carte primaire."],
        "inputs": "Modèle + carte déposée (+ FSC demi-cartes pour superposer les courbes).",
        "tips": [
            "Q-score moyen proche de la valeur attendue à cette résolution : bon accord ; résidus avec Q < 0,3 : à revoir.",
            "La FSC carte–modèle à 0,5 doit être proche de la résolution FSC demi-cartes à 0,143.",
        ],
        "next": ["isolde_session", "checkmysequence", "predeposition_check"],
    },
    # --------------------------------------------------------------- Deposition
    "predeposition_check": {
        "purpose": "Checklist automatique avant OneDep : cohérence cartes/demi-cartes/masque, modèle dans la boîte, ajustement, "
                   "chaînes, résidus inconnus, occupations, B-factors, clashs sévères, ruptures de chaîne, accord avec la séquence.",
        "when": ["Avant chaque soumission ; après tout changement de carte, de masque ou de modèle."],
        "avoid": [],
        "inputs": "Modèle final + carte primaire + demi-cartes + masque + séquence.",
        "tips": ["Corrigez les FAIL ; chaque WARN doit être compris et, si besoin, justifié auprès des annotateurs."],
        "next": ["deposition_package", "wwpdb_validation"],
    },
    "deposition_package": {
        "purpose": "Assemble tout le nécessaire au dépôt wwPDB/EMDB : mmCIF, cartes, FSC XML, rapports, niveau de contour "
                   "recommandé, brouillon des méthodes avec citations et brouillon de « Table 1 ».",
        "when": ["Le modèle est final et validé ; préparation de l'article."],
        "avoid": ["Modèle encore en cours d'affinement : refaites le paquet à la fin."],
        "inputs": "Modèle final, carte primaire, demi-cartes, masque, FSC, checklist.",
        "tips": [
            "Si le traitement a été fait dans CryoPlug avec RELION (films → carte, ou séries d'inclinaison → moyenne), "
            "methods_draft.md décrit chaque étape avec ses réglages et ses résultats (films, mouvement, CTF, picking, "
            "extraction, classifications, raffinements, CTF refinement, polishing, post-traitement) et les articles à "
            "citer, et table1_draft.md se remplit : tension, exposition totale, plage de défocus (celle estimée par "
            "CTFFIND), taille de pixel, particules initiales et finales, facteur B, plage de résolution locale.",
            "Relisez methods_draft.md et table1_draft.md : le grandissement et ce que CryoPlug n'a pas vu passer restent à "
            "remplir.",
            "Lancez aussi le job « wwPDB validation report » : le rapport officiel, celui que verront les relecteurs.",
        ],
        "next": [],
    },
    # ---------------------------------------------------------------- Utilities
    "model_tools": {
        "purpose": "Édition de modèle : conversion PDB↔mmCIF, sélection/renommage de chaînes, suppression d'hydrogènes, eaux, "
                   "ligands ou conformations alternatives, remise à zéro des B-factors/occupations.",
        "when": [
            "Préparer l'entrée d'un logiciel exigeant (format, hydrogènes).",
            "Renommer les chaînes comme dans l'article, retirer des copies ou un partenaire.",
        ],
        "avoid": ["Remettre des B-factors à une valeur unique sur le modèle final déposé."],
        "inputs": "Le modèle.",
        "tips": ["Le mmCIF est recommandé (identifiants de chaîne longs, grands complexes)."],
        "next": ["phenix_real_space_refine"],
    },
    "merge_models": {
        "purpose": "Combine les chaînes de plusieurs modèles (domaines ou sous-unités dockés séparément, ligand) en un seul.",
        "when": ["Après docking domaine par domaine ; ajout d'un ligand (coordonnées eLBOW) ou d'une sous-unité."],
        "avoid": ["Modèles qui se chevauchent dans l'espace : vérifiez dans le visualiseur."],
        "inputs": "Deux à quatre modèles.",
        "tips": ["Les identifiants de chaîne en conflit sont renommés automatiquement (voir le log)."],
        "next": ["phenix_real_space_refine", "isolde_session"],
    },
    "chimerax_render": {
        "purpose": "Images ChimeraX hors écran du modèle et/ou de la carte sous trois vues orthogonales.",
        "when": ["Figures rapides, rapports de labo, suivi d'un projet."],
        "avoid": ["Figures finales de publication : faites-les dans ChimeraX interactif."],
        "inputs": "Carte et/ou modèle.",
        "tips": ["Nécessite une version de ChimeraX qui supporte --offscreen."],
        "next": [],
    },
    "custom_command": {
        "purpose": "Lance n'importe quel programme sur les données du projet (script bash avec des emplacements {map}, "
                   "{half_map_a}, {model}…), et enregistre les fichiers produits comme sorties.",
        "when": ["Logiciel pas encore intégré (DeepMainmast, EModelX, CryoREAD, scripts maison…)."],
        "avoid": ["Opérations déjà couvertes par un job dédié (meilleure traçabilité et rapports).",
                  "Avec des comptes utilisateurs, réservé aux administrateurs : le script s'exécute sous le compte du serveur."],
        "inputs": "Les entrées utiles au programme ; choisissez l'environnement (configuration d'un outil).",
        "tips": ["Indiquez les motifs de sortie (ex. result*.mrc) pour récupérer cartes et modèles."],
        "next": [],
    },
    # ------------------------------------------------------------------ tomographie (RELION 5)
    "relion_tomo_import": {
        "purpose": "Importe des séries d'inclinaison enregistrées avec SerialEM (relion_python_tomo_import SerialEM) : les "
                   "films (ou images déjà corrigées) de chaque inclinaison et le fichier mdoc de chaque série, qui donne les "
                   "angles, l'ordre d'acquisition (donc la dose reçue avant chaque image) et la défocalisation nominale. Le "
                   "point de départ de la moyenne de sous-tomogrammes.",
        "when": [
            "Au début d'un projet de tomographie (données SerialEM : un fichier .mdoc par série, comme EMPIAR-10164).",
            "Images déjà corrigées du mouvement ailleurs : cochez « Images already motion-corrected ».",
        ],
        "avoid": [
            "Un fichier mdoc dont aucune image n'est parmi les fichiers : le job s'arrête (RELION 5.1 associerait alors les "
            "autres séries aux mauvais fichiers mdoc) ; restreignez le motif des mdoc.",
            "Une mauvaise main de défocalisation donne une carte en miroir : « Invert defocus handedness » oui pour les "
            "données du tutoriel RELION, à déterminer une fois pour votre microscope.",
        ],
        "inputs": "Aucune : un motif sur les films et un sur les fichiers mdoc ; taille de pixel, tension, Cs, contraste "
                  "d'amplitude et dose par image.",
        "tips": [
            "La dose : par image (les mdoc donnent l'ordre d'acquisition) ou par trame (« The dose above is per movie "
            "frame ») ; 0 = les ExposureDose des mdoc (que RELION 5.1 cumule en comptant l'image elle-même).",
            "Le préfixe (plusieurs sessions aux mêmes noms de séries) est ajouté par CryoPlug après l'import : l'option "
            "--prefix de RELION 5.1.1 ne trouve plus aucune série.",
            "Un avertissement compte les images nommées dans les mdoc mais absentes des fichiers (RELION les ignore).",
        ],
        "next": ["relion_tomo_motioncorr", "relion_tomo_ctffind"],
    },
    "relion_tomo_motioncorr": {
        "purpose": "Corrige le mouvement de chaque film d'inclinaison (relion_run_motioncorr sur un jeu de séries ; "
                   "implémentation de RELION sur CPU ou MotionCor2) : les images d'inclinaison, leur mouvement et les "
                   "spectres de puissance pour CTFFIND ; en option les sommes des trames paires et impaires pour le "
                   "débruitage.",
        "when": [
            "Juste après l'import des films.",
            "Cochez « Save images for denoising » si vous comptez débruiter les tomogrammes avec cryoCARE.",
        ],
        "avoid": [
            "Des séries importées comme images déjà corrigées : passez directement à l'estimation de CTF.",
            "Pas de pondération de dose ici : RELION l'applique dans l'extraction et la reconstruction.",
        ],
        "inputs": "Les séries d'inclinaison importées (films).",
        "tips": [
            "Un seul patch (1 × 1) suffit souvent pour des films de tomographie à faible dose.",
            "float16 : moitié moins de disque ; CTFFIND lit alors les spectres de puissance (sauvés d'office).",
            "Les fichiers de séries des programmes C++ de RELION portent le nom du job puis celui de l'entrée "
            "(J3/J2/tilt_series/TS_01.star) : c'est normal dans un projet CryoPlug.",
        ],
        "next": ["relion_tomo_ctffind"],
    },
    "relion_tomo_ctffind": {
        "purpose": "Ajuste la CTF de chaque image d'inclinaison avec CTFFIND (relion_run_ctffind sur un jeu de séries) : "
                   "recherche autour de la défocalisation nominale des mdoc, résolution maximale de l'ajustement relâchée "
                   "avec la dose reçue (les anneaux de Thon s'estompent).",
        "when": ["Après la correction de mouvement, avant l'exclusion des images et l'alignement."],
        "avoid": [
            "CTFFIND non configuré (page Tools) : le job échoue.",
            "Une recherche trop étroite autour de la défocalisation nominale si le microscope s'écarte de la consigne : "
            "élargissez « Search around the nominal defocus ».",
        ],
        "inputs": "Les séries corrigées du mouvement.",
        "tips": [
            "« Dose-dependent Thon ring fading » = 100 e⁻/Å² (défaut de RELION) : résolution maximale × exp(dose / 100).",
            "Les graphiques défocalisation et résolution contre l'angle d'inclinaison montrent d'un coup d'œil les "
            "images ratées ; le navigateur des séries montre chaque image avec son ajustement.",
            "L'épaisseur de CTFFIND5 n'est pas prise en charge par le programme de RELION 5.1.",
        ],
        "next": ["relion_tomo_exclude", "relion_tomo_align"],
    },
    "relion_tomo_exclude": {
        "purpose": "Retire les mauvaises images de chaque série (bord de grille ou de lamelle dans le faisceau, glace "
                   "cristalline, dérive, CTF ratée), comme l'outil napari de RELION : les images par angle avec leur "
                   "ajustement de CTF, un clic pour exclure ou garder, et des limites sur l'angle, la dose et la qualité "
                   "de la CTF. Écrit selected_tilt_series.star comme RELION.",
        "when": [
            "Après l'estimation de CTF, avant l'alignement : une image ratée gâche l'alignement de toute la série.",
            "Mode « By the limits below only » pour un traitement automatique (agents, flux de travail).",
        ],
        "avoid": [
            "Exclure trop d'images aux grands angles : le cône manquant s'élargit ; gardez ±40° au moins si possible.",
            "Une série sans aucune image restante est retirée (avertissement).",
        ],
        "inputs": "Les séries corrigées du mouvement, de préférence avec leur CTF.",
        "tips": [
            "Les limites pré-sélectionnent les images à exclure ; vous corrigez à la main (clic, X ou espace ; ← → : "
            "images ; ↑ ↓ : séries).",
            "Le travail en cours est sauvegardé pendant que le job attend ; « Done » écrit les séries.",
            "« Worst CTF fit resolution » = 15-20 Å retire les images dont l'ajustement a échoué (RELION plafonne à 25 Å).",
        ],
        "next": ["relion_tomo_align"],
    },
    "relion_tomo_align": {
        "purpose": "Aligne les images de chaque série (relion_align_tiltseries) : alignement d'IMOD sur billes d'or, suivi "
                   "de patchs d'IMOD (sans billes) ou appariement de projections d'AreTomo2 sur GPU, qui peut aussi "
                   "estimer la CTF et reconstruire les tomogrammes.",
        "when": [
            "Après l'exclusion des mauvaises images.",
            "Billes d'or : IMOD fiducials ; lamelles FIB ou échantillons sans billes : AreTomo2 ou le suivi de patchs.",
        ],
        "avoid": [
            "AreTomo2 sans GPU : impossible.",
            "Une épaisseur très mal estimée : l'appariement de projections et le suivi de patchs en dépendent.",
        ],
        "inputs": "Les séries (avec leur CTF), de préférence après l'exclusion des mauvaises images.",
        "tips": [
            "Le tableau donne l'erreur résiduelle d'IMOD (nm) ou le score d'axe d'AreTomo2 de chaque série : une série "
            "mal alignée se voit tout de suite.",
            "« Correct the tilt angle offset » (AreTomo -TiltCor) pour les lamelles fraisées en biais.",
            "Avec « Reconstruct the tomograms », la sortie est directement un jeu de tomogrammes.",
            "AreTomo2, IMOD (batchruntomo) et cryoCARE se configurent dans Tools ; sinon RELION les cherche dans son PATH.",
        ],
        "next": ["relion_tomo_reconstruct"],
    },
    "relion_tomo_reconstruct": {
        "purpose": "Reconstruit un tomogramme binné de chaque série alignée (relion_tomo_reconstruct_tomogram), corrigé de "
                   "la CTF par inversion de Fourier avec un filtre de Wiener, pour regarder les données et pointer les "
                   "particules (la moyenne repart des images d'inclinaison). En option les tomogrammes des trames paires "
                   "et impaires (débruitage) et une projection 2D d'une tranche centrale.",
        "when": ["Après l'alignement.", "Pour cryoCARE : « Tomograms for denoising » (sans inversion de Fourier)."],
        "avoid": [
            "« Tomograms for denoising » avec l'inversion de Fourier : refusé (RELION ne reconstruit alors que la série "
            "entière).",
            "Une épaisseur bien plus grande que l'échantillon : du vide inutile et un débruitage moins bon.",
        ],
        "inputs": "Les séries alignées (avec leur CTF).",
        "tips": [
            "5-10 Å par voxel suffisent pour voir les particules ; largeur et hauteur à 0 : celles des images.",
            "Pour le débruitage, RELION n'écrit que les deux moitiés (rec_*_half1/2.mrc) : la visionneuse montre la "
            "première, cryoCARE prend les deux.",
            "« Only this tomogram » : RELION n'écrit pas de jeu de tomogrammes pour un seul tomogramme ; CryoPlug l'écrit.",
            "Visionneuse : molette ou ↑ ↓ pour la coupe, une tranche de plusieurs coupes moyennées, un filtre passe-bas.",
        ],
        "next": ["relion_tomo_denoise", "relion_tomo_pick", "pytom_template_match", "relion_tomo_import_coords"],
    },
    "relion_tomo_denoise": {
        "purpose": "Débruite les tomogrammes avec cryoCARE (relion_python_tomo_denoise) : un réseau apprend, sur les "
                   "tomogrammes des trames paires et impaires de quelques séries (Noise2Noise), à retirer le bruit, puis "
                   "débruite chaque tomogramme. Le pointage devient bien plus facile ; la moyenne utilise toujours les "
                   "images brutes.",
        "when": ["Avant le pointage, surtout pour de petites particules ou un environnement encombré (in situ)."],
        "avoid": [
            "Des tomogrammes reconstruits sans « Tomograms for denoising » : pas de moitiés, impossible.",
            "Mesurer sur le tomogramme débruité : il sert à voir et à pointer.",
        ],
        "inputs": "Les tomogrammes reconstruits pour le débruitage ; pour « Denoise with a trained model », ceux d'un job "
                  "d'entraînement (son modèle est repris).",
        "tips": [
            "Entraînement sur 2-3 tomogrammes couvrant la plage de défocalisation (vide : trois répartis dans le jeu).",
            "« Tiles » : augmentez-les si le GPU manque de mémoire (4,4,2…).",
            "Le modèle (denoising_model.tar.gz) d'un entraînement sert aussi à débruiter d'autres jeux.",
        ],
        "next": ["relion_tomo_pick"],
    },
    "relion_tomo_pick": {
        "purpose": "Pointe les particules dans les tomogrammes, coupe par coupe, comme le pointeur napari de RELION : "
                   "particules isolées (un clic chacune), sphères (vésicules, capsides : un centre et un rayon, particules "
                   "réparties sur la surface) ou filaments (des points le long de chacun, particules le long de la courbe), "
                   "avec leurs orientations par rapport à la surface ou à l'axe. Écrit les annotations et le jeu de "
                   "particules que RELION en dérive (particles.star, optimisation_set.star).",
        "when": [
            "Après la reconstruction (de préférence débruitée).",
            "Sphères et filaments : des a priori d'orientation qui rendent les raffinements bien plus robustes.",
        ],
        "avoid": ["Corriger d'anciens pointages hors du mode « Particles » (seul mode qui les reprend)."],
        "inputs": "Les tomogrammes (débruités de préférence) ; en option les particules d'un pointage précédent, à "
                  "compléter ou corriger.",
        "tips": [
            "Molette ou ↑ ↓ : coupe ; Ctrl + molette : zoom ; glisser : déplacer ; ← → : tomogrammes.",
            "Les cercles montrent la section, sur la coupe affichée, de la sphère du diamètre de la particule (trait plein : "
            "centrée sur cette coupe).",
            "Sphères : un clic au centre sur la coupe médiane, puis un point du bord ; « Particle spacing » fixe la "
            "distance entre les particules réparties sur la surface.",
            "Filaments : des points sur les coupes où il passe, Entrée pour le finir ; polarité inconnue : les deux sens "
            "sont raffinés (rlnAnglePsiFlipRatio 0,5).",
            "Le travail est sauvegardé pendant que le job attend ; « Done » écrit des fichiers lisibles aussi par RELION.",
        ],
        "next": ["relion_tomo_extract", "relion_tomo_reconstruct_particle"],
    },
    "pytom_template_match": {
        "purpose": "Trouve les particules d'une structure connue dans les tomogrammes par template matching "
                   "(pytom-match-pick, sur GPU) : le modèle, une carte de la particule, est corrélé à chaque tomogramme "
                   "dans toutes les orientations, pondéré image par image avec les angles, défocus et doses des séries "
                   "RELION ; les meilleurs pics deviennent un jeu de particules RELION 5 (positions, angles, scores). Le "
                   "pointage automatique qui manque au pipeline de RELION 5.",
        "when": [
            "Une particule de structure connue (ribosome, protéasome, complexe déjà résolu) dans des tomogrammes in situ.",
            "Remplacer ou compléter le pointage à la main sur beaucoup de tomogrammes.",
        ],
        "avoid": [
            "Des tomogrammes à pleine résolution : binnez 6-8 fois (10-20 Å par voxel), sinon c'est très long.",
            "Un modèle de contraste opposé : la protéine est sombre dans les tomogrammes, blanche dans les cartes "
            "(« Invert the template contrast », coché par défaut).",
            "Des tomogrammes débruités : le bruit sur lequel on juge les scores change ; gardez les tomogrammes bruts.",
        ],
        "inputs": "Les tomogrammes reconstruits (avec leurs séries et la CTF), une carte de la particule ; un masque en option "
                  "(sinon une sphère 15 % plus large que la particule).",
        "tips": [
            "Le diamètre de la particule fixe le masque, l'échantillonnage angulaire (critère de Crowther) et la distance "
            "minimale entre deux pics ; le modèle est mis à la taille de voxel des tomogrammes (pytom_create_template.py).",
            "La coupure sur le score vient du nombre de faux positifs admis par tomogramme (1 par défaut) ; ou donnez "
            "une coupure fixe (LCCmax). Le filtre tophat ne garde que les pics nets (moins de membranes et de bords).",
            "Tomogrammes de RELION reconstruits par inversion de Fourier : déjà corrigés de la CTF, la CTF du modèle est "
            "alors retournée en phase (--tomogram-ctf-model phase-flip, détecté tout seul).",
            "Symétrie Cn d'axe z (convention de relion_align_symmetry) : n fois moins de rotations à essayer.",
            "Le rapport montre une coupe du tomogramme et de la carte des scores, et l'histogramme des scores : une "
            "séparation nette entre les vraies particules et le bruit est bon signe.",
            "Les particules gardent les angles du meilleur modèle ; extrayez-les, puis modèle initial ou raffinement.",
        ],
        "next": ["relion_tomo_extract", "relion_tomo_pick", "relion_tomo_reconstruct_particle"],
    },
    "relion_tomo_import_coords": {
        "purpose": "Importe des positions de particules pointées ailleurs (crYOLO, PyTom, Dynamo, template matching, "
                   "modèles IMOD convertis) : fichiers STAR ou texte à 3 colonnes (X Y Z), ou 6 avec les angles du "
                   "sous-tomogramme, un fichier par tomogramme nommé d'après lui (relion_tomo_import_coordinates).",
        "when": ["Un pointage fait par un autre logiciel (apprentissage profond, template matching)."],
        "avoid": [
            "Un seul fichier correspondant au motif : RELION le lit comme la liste STAR des fichiers de coordonnées "
            "(rlnTomoName, rlnTomoImportParticleFile).",
            "Des coordonnées binnées sans facteur : multipliez-les par le binning (« Multiply the coordinates by »).",
        ],
        "inputs": "Les tomogrammes auxquels appartiennent les coordonnées.",
        "tips": [
            "« Remove from the file names » : la partie à retirer du nom de fichier pour retrouver le tomogramme "
            "(TS_01_coords.txt → TS_01).",
            "Coordonnées centrées (Å depuis le centre) : cochez l'option ; sinon pixels de la série non binnée.",
            "CryoPlug associe les particules aux tomogrammes de l'entrée dans un optimisation_set.star.",
        ],
        "next": ["relion_tomo_extract", "relion_tomo_pick"],
    },
    "relion_tomo_extract": {
        "purpose": "Extrait les particules des séries d'inclinaison (relion_tomo_subtomo) : pour chacune les images des "
                   "inclinaisons qui la voient, prémultipliées par la CTF, en pile d'images 2D (RELION 5), en "
                   "pseudo-sous-tomogramme 3D (RELION 4) ou en sous-tomogramme réel, binnées à la boîte voulue.",
        "when": ["Après le pointage ; à nouveau à pleine résolution après les premiers raffinements binnés."],
        "avoid": [
            "Une boîte trop petite : la délocalisation de la CTF coupe du signal (prenez une pré-découpe plus grande).",
            "Pleine résolution d'emblée : des raffinements lents ; commencez binné (4-6 Å/px).",
        ],
        "inputs": "Les particules pointées (ou raffinées).",
        "tips": [
            "Boîte ≈ 1,5-2 × la particule ; « Binned box » = cette boîte en pixels après binning (Nyquist = 2 × pixel).",
            "Piles 2D (RELION 5) : moins de disque, recommandé ; sous-tomogrammes réels pour un premier alignement "
            "difficile.",
            "« Highest dose » et « Fewest tilts » écartent les images trop irradiées et les particules trop peu vues.",
        ],
        "next": ["relion_tomo_reconstruct_particle", "relion_tomo_initial_model", "relion_tomo_refine3d",
                 "relion_tomo_class3d"],
    },
    "relion_tomo_reconstruct_particle": {
        "purpose": "Reconstruit la moyenne des particules à partir des images d'inclinaison, dans leurs orientations "
                   "actuelles (relion_tomo_reconstruct_particle) : la carte complète et, avec des demi-jeux, les deux "
                   "demi-cartes, à n'importe quels binning et boîte.",
        "when": [
            "Juste après un pointage sur sphères ou filaments : une référence de départ (orientations de la géométrie).",
            "Après la CTF refinement ou l'alignement des trames : la carte améliorée, à pleine taille.",
        ],
        "avoid": ["Des particules sans demi-jeux (pas encore raffinées) : une seule carte, ni demi-cartes ni FSC."],
        "inputs": "Les particules ; un masque pour la FSC masquée.",
        "tips": [
            "La boîte et le binning portent sur les images d'inclinaison : la carte peut être plus grande ou plus fine "
            "que les sous-tomogrammes extraits.",
            "La symétrie s'applique pendant la reconstruction ; la symétrie hélicoïdale après (relion_helix_toolbox).",
        ],
        "next": ["relion_tomo_refine3d", "relion_mask_create", "relion_postprocess"],
    },
    "relion_tomo_initial_model": {
        "purpose": "Une première carte à partir des sous-tomogrammes, sans référence (relion_refine --grad --denovo_3dref "
                   "--ios, VDAM), alignée sur les axes de symétrie et symétrisée : l'ab initio de RELION 5 pour la "
                   "tomographie.",
        "when": ["Aucune référence (ni structure connue, ni moyenne tirée de la géométrie du pointage)."],
        "avoid": ["Des particules pointées sur des surfaces avec des a priori fiables : une moyenne directe est plus sûre."],
        "inputs": "Les sous-tomogrammes extraits (binnés de préférence).",
        "tips": [
            "« Prior width on the tilt angle » : pour des particules pointées sur des sphères ou des filaments, la "
            "recherche reste à ± 3 × cette valeur de l'a priori.",
            "Pour le reste, comme le modèle initial en analyse de particules isolées.",
        ],
        "next": ["relion_tomo_refine3d", "relion_tomo_class3d"],
    },
    "relion_tomo_class3d": {
        "purpose": "Trie les sous-tomogrammes en classes 3D contre une référence (relion_refine --K --ios) : recherches "
                   "globales, locales ou aucune. Chaque classe donne une carte et ses particules.",
        "when": ["Hétérogénéité de conformation ou de composition ; tri des faux positifs du pointage."],
        "avoid": ["Trop de classes pour peu de particules : des classes vides ou bruitées."],
        "inputs": "Les sous-tomogrammes (ou des particules raffinées) et une référence.",
        "tips": [
            "Sans alignement (après un raffinement) : rapide, le tri porte sur la structure seule.",
            "« Prior width on the tilt angle » garde les orientations près de la surface ou de l'axe des filaments.",
        ],
        "next": ["relion_tomo_refine3d", "relion_tomo_reconstruct_particle"],
    },
    "relion_tomo_refine3d": {
        "purpose": "Raffinement 3D gold-standard des sous-tomogrammes contre une référence (relion_refine --auto_refine "
                   "--ios) : recherches globales puis locales, deux demi-jeux indépendants. Donne la carte, les "
                   "demi-cartes, la FSC et les particules raffinées (pour la CTF refinement et l'alignement des trames).",
        "when": [
            "Après l'extraction et un modèle initial ou une moyenne ; à nouveau après la CTF refinement, l'alignement des "
            "trames ou une ré-extraction à pleine résolution.",
        ],
        "avoid": [
            "Moins de 3 processus MPI : impossible.",
            "Une référence d'une autre boîte ou d'un autre pixel que les sous-tomogrammes : reconstruisez-la à la même "
            "boîte.",
        ],
        "inputs": "Les sous-tomogrammes et une référence (modèle initial, moyenne, carte connue filtrée).",
        "tips": [
            "Avec des a priori (sphères, filaments), « Prior width on the tilt angle » de 5-10° stabilise la recherche.",
            "Le jeu d'optimisation écrit (run_optimisation_set.star) garde les tomogrammes et trajectoires de l'entrée.",
        ],
        "next": ["relion_mask_create", "relion_postprocess", "relion_tomo_ctf_refine", "relion_tomo_frame_align",
                 "relion_tomo_reconstruct_particle"],
    },
    "relion_tomo_ctf_refine": {
        "purpose": "Affine la défocalisation de chaque image d'inclinaison et l'échelle de contraste (épaisseur de glace) "
                   "contre la référence (relion_tomo_refine_ctf). Reconstruisez ou raffinez ensuite à nouveau.",
        "when": ["Après un raffinement à résolution intermédiaire (meilleure que 10 Å) : des gains nets ensuite."],
        "avoid": ["Une référence à basse résolution : rien à gagner.", "Échelle par image et par tomogramme : exclusives."],
        "inputs": "Les particules raffinées, les demi-cartes de ce raffinement, un masque et la carte post-traitée de "
                  "RELION (sa FSC pondère les fréquences).",
        "tips": [
            "Recherche de ±3000 Å ; la régularisation garde des défocalisations voisines dans une série (les grands "
            "angles portent peu de signal).",
            "CryoPlug passe --per_tomogram_scale, l'option que lit le programme (le GUI de RELION 5.1 passe "
            "--per_tomo_scale, ignorée).",
            "Le jeu de tomogrammes mis à jour suit les particules dans optimisation_set.star.",
        ],
        "next": ["relion_tomo_frame_align", "relion_tomo_reconstruct_particle", "relion_tomo_refine3d"],
    },
    "relion_tomo_frame_align": {
        "purpose": "Affine l'alignement de chaque image d'inclinaison contre la référence (relion_tomo_align) : décalages "
                   "et angles des images, positions 3D des particules et, en option, leur mouvement au fil de la série "
                   "(le polissage bayésien des sous-tomogrammes). Reconstruisez ou raffinez ensuite à nouveau.",
        "when": [
            "Après la CTF refinement, pour les derniers gains de résolution.",
            "« Align by shift only » pour des séries très mal alignées.",
        ],
        "avoid": ["Le mouvement par particule et l'alignement par décalage seul ensemble : impossible."],
        "inputs": "Les particules raffinées (après la CTF refinement de préférence), les demi-cartes, un masque, la carte "
                  "post-traitée.",
        "tips": [
            "« Fit per-particle motion » : des trajectoires 3D (motion.star) que la reconstruction et le raffinement "
            "suivants appliquent.",
            "Plusieurs cycles CTF refinement → alignement des trames → raffinement améliorent encore la carte.",
        ],
        "next": ["relion_tomo_reconstruct_particle", "relion_tomo_refine3d", "relion_tomo_ctf_refine"],
    },
}

FIELDS = ("purpose", "when", "avoid", "inputs", "tips", "next")
SECTION_TITLES = {
    "when": "Quand l'utiliser",
    "avoid": "À éviter / pièges",
    "inputs": "Entrées conseillées",
    "tips": "Conseils",
    "next": "Étapes suivantes",
}
CATEGORY_TITLES_FR = {
    "Import": "Import",
    "Preprocessing": "Prétraitement (films, CTF)",
    "Picking": "Sélection et extraction de particules",
    "Classification": "Classification 2D / 3D",
    "Reconstruction": "Reconstruction (RELION)",
    "Tomography": "Tomographie (sous-tomogrammes, RELION 5)",
    "Map processing": "Traitement de carte",
    "Heterogeneity": "Hétérogénéité (variabilité 3D)",
    "Model building": "Construction de modèle",
    "Interactive": "Reconstruction interactive",
    "Refinement": "Affinement",
    "Validation": "Validation",
    "Deposition": "Dépôt",
    "Utilities": "Utilitaires",
}


def job_help(name: str) -> dict[str, Any] | None:
    return HELP.get(name)


def render_markdown() -> str:
    """Référence des jobs au format Markdown (docs/JOBS.md)."""
    from cryoplug.jobs import CATEGORIES, all_job_types

    titles = {jt.name: jt.title for jt in all_job_types()}
    lines = [
        "# Référence des jobs CryoPlug",
        "",
        "Fichier généré par `cryoplug docs-jobs` à partir de `cryoplug/jobhelp.py` — ne pas éditer à la main.",
        "Les mêmes fiches sont affichées dans l'interface (constructeur de job, détails, page d'aide).",
        "",
    ]
    for cat in CATEGORIES:
        jobs = [jt for jt in all_job_types() if jt.category == cat]
        if not jobs:
            continue
        lines += [f"- [{CATEGORY_TITLES_FR.get(cat, cat)}](#{_anchor(CATEGORY_TITLES_FR.get(cat, cat))})"]
    lines.append("")
    for cat in CATEGORIES:
        jobs = [jt for jt in all_job_types() if jt.category == cat]
        if not jobs:
            continue
        lines += [f"## {CATEGORY_TITLES_FR.get(cat, cat)}", ""]
        for jt in jobs:
            h = HELP[jt.name]
            tool = f" — logiciel : `{jt.tool}`" if jt.tool else " — intégré (aucun logiciel externe)"
            gpu = " · GPU" if jt.gpu else ""
            lines += [f"### {jt.title}", "", f"`{jt.name}`{tool}{gpu}", "", h["purpose"], ""]
            for key in ("when", "avoid"):
                if h[key]:
                    lines += [f"**{SECTION_TITLES[key]}**", ""] + [f"- {x}" for x in h[key]] + [""]
            lines += [f"**{SECTION_TITLES['inputs']}** : {h['inputs']}", ""]
            if h["tips"]:
                lines += [f"**{SECTION_TITLES['tips']}**", ""] + [f"- {x}" for x in h["tips"]] + [""]
            if h["next"]:
                lines += [f"**{SECTION_TITLES['next']}** : " + ", ".join(titles.get(n, n) for n in h["next"]), ""]
    return "\n".join(lines).rstrip() + "\n"


def _anchor(text: str) -> str:
    """GitHub-style heading anchor (accents are kept, punctuation removed, spaces become hyphens)."""
    return "".join(c for c in text.lower() if c.isalnum() or c in " -").strip().replace(" ", "-")
