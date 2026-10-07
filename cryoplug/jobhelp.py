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
        "next": ["directional_fsc", "locscale", "modelangelo_build", "cryoatom_build", "import_sequence"],
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
        "next": ["phenix_process_predicted_model", "chimerax_fitmap", "phenix_dock_in_map", "mapmodel_validation"],
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
        "next": ["modelangelo_build", "cryoatom_build", "boltz_predict", "colabfold_predict"],
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
                   "l'orientation préférentielle des particules.",
        "when": [
            "La distribution angulaire de CryoSPARC montre des vues dominantes.",
            "La densité paraît étirée selon un axe, ou les hélices sont nettes dans un plan et floues dans un autre.",
            "Avant de décider d'un spIsoNet ou d'une nouvelle collecte inclinée (tilt).",
        ],
        "avoid": [
            "Très petites particules : chaque cône contient peu de coefficients, le résultat est bruité.",
            "Ce n'est pas une résolution locale (par région) mais par direction.",
        ],
        "inputs": "Demi-cartes non filtrées et masque FSC.",
        "tips": [
            "Rapport pire/meilleure direction ≤ 1,25 : isotrope ; 1,25–1,6 : modéré ; > 1,6 : anisotropie forte.",
            "Élévation 90° = axe z de la carte ; directions opposées équivalentes (hémisphère).",
        ],
        "next": ["spisonet", "locscale"],
    },
    "map_tools": {
        "purpose": "Opérations simples sur une carte : taille de pixel, recadrage/agrandissement de boîte, inversion de main, "
                   "B-factor global, filtre passe-bas, multiplication par un masque, normalisation.",
        "when": [
            "La main est fausse (hélices gauches, ModelAngelo ne construit que des fragments) : « Flip handedness ».",
            "Boîte non cubique ou trop grande pour un logiciel : recadrer en boîte cubique.",
            "Carte filtrée à une résolution donnée pour une figure ou un ajustement grossier.",
        ],
        "avoid": ["Recadrer la carte déposée sans recadrer de même les demi-cartes et le masque."],
        "inputs": "Une carte ; le masque seulement pour « Multiply by mask ».",
        "tips": [
            "Les opérations s'enchaînent dans un ordre fixe : pixel → boîte → main → B-factor → filtre → masque → normalisation.",
            "B-factor négatif = affûtage, positif = flou.",
        ],
        "next": ["modelangelo_build", "cryoatom_build", "chimerax_fitmap"],
    },
    "create_mask": {
        "purpose": "Crée un masque à bord doux : à partir de la densité (passe-bas + seuil), des atomes de chaînes choisies, "
                   "ou de la densité proche de ces atomes. Dilatation et bord doux en Å, comme dans CryoSPARC ; "
                   "combinaison avec un autre masque (union, soustraction, intersection).",
        "when": [
            "Masque de raffinement local d'une sous-partie d'un gros complexe (chaînes choisies + densité voisine).",
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
            "FSC / LocScale : densité filtrée à 15 Å, dilatation 3 Å, bord 6 Å.",
            "Donnez la masse attendue (kDa) pour un seuil automatique fiable ; sinon la masse des atomes choisis est utilisée.",
            "Les coupes du rapport montrent le masque (orange) sur la carte : vérifiez qu'il ne coupe pas de densité.",
            "Le chemin du fichier est donné pour l'importer dans CryoSPARC (Import 3D Volumes, type mask).",
        ],
        "next": ["map_fsc", "locscale", "spisonet", "local_resolution"],
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
        "next": ["modelangelo_build", "cryoatom_build", "chimerax_fitmap", "isolde_session"],
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
        "next": ["chimerax_fitmap", "isolde_session"],
    },
    "emready": {
        "purpose": "Amélioration de carte par apprentissage profond combinant informations locales et non locales.",
        "when": ["Alternative à DeepEMhancer/EMmerNet pour l'interprétation, notamment le tracé de la chaîne principale."],
        "avoid": ["Mêmes limites que les autres réseaux : interprétation seulement, pas de validation ni de dépôt comme carte primaire."],
        "inputs": "La carte (non affûtée ou affûtée), masque optionnel.",
        "tips": ["Un pas (stride) plus petit est plus précis mais plus lent."],
        "next": ["chimerax_fitmap", "modelangelo_build"],
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
        "next": ["modelangelo_build", "cryoatom_build", "isolde_session"],
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
        "next": ["modelangelo_build", "cryoatom_build", "phenix_real_space_refine"],
    },
    "phenix_auto_sharpen": {
        "purpose": "Affûtage global optimisé pour maximiser le détail et la connectivité (phenix.auto_sharpen).",
        "when": ["La carte affûtée de CryoSPARC semble sur- ou sous-affûtée.", "Vous partez d'une carte non affûtée d'un autre logiciel."],
        "avoid": ["Résolution locale très hétérogène : préférez LocScale ou l'affûtage local anisotrope."],
        "inputs": "La carte NON affûtée ; les demi-cartes sont optionnelles.",
        "tips": ["Comparez visuellement avec la carte CryoSPARC dans le visualiseur 3D avant de choisir."],
        "next": ["modelangelo_build", "chimerax_fitmap"],
    },
    "phenix_local_aniso_sharpen": {
        "purpose": "Affûtage local et anisotrope avec mise à l'échelle dépendant de la résolution, à partir des demi-cartes "
                   "(éventuellement guidé par un modèle).",
        "when": ["Résolution variable avec une légère anisotropie ; alternative Phenix à LocScale."],
        "avoid": ["Anisotropie forte : envisagez spIsoNet."],
        "inputs": "Demi-cartes non filtrées, modèle optionnel.",
        "tips": ["Avec un modèle, le résultat suit mieux la résolution locale réelle."],
        "next": ["modelangelo_build", "phenix_real_space_refine"],
    },
    # ----------------------------------------------------------- Model building
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
        "next": ["cryoatom_build", "modelangelo_build"],
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
        "next": ["phenix_process_predicted_model", "chimerax_fitmap"],
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
        "next": ["phenix_process_predicted_model", "chimerax_fitmap", "phenix_dock_in_map"],
    },
    "phenix_process_predicted_model": {
        "purpose": "Prépare un modèle prédit : retire les résidus de faible confiance (pLDDT), convertit le pLDDT en B-factors, "
                   "découpe éventuellement en domaines compacts.",
        "when": ["Toujours avant d'ajuster ou d'affiner un modèle AlphaFold/ColabFold/Boltz.", "Domaines mobiles : découpe pour les ajuster séparément."],
        "avoid": ["Modèle expérimental : ses B-factors ne sont pas des pLDDT."],
        "inputs": "Le modèle prédit.",
        "tips": ["pLDDT minimal 70 par défaut ; baissez-le si vous perdez des régions importantes bien définies dans la carte."],
        "next": ["chimerax_fitmap", "phenix_dock_in_map"],
    },
    "phenix_dock_in_map": {
        "purpose": "Recherche globale de la position d'un modèle (ou d'un domaine) dans la carte (phenix.dock_in_map).",
        "when": ["Position inconnue, plusieurs copies possibles, carte de résolution moyenne à basse."],
        "avoid": ["Modèle déjà presque en place : un ajustement local (ChimeraX fitmap) suffit et va plus vite."],
        "inputs": "Modèle (traité s'il est prédit) et carte.",
        "tips": ["Dockez domaine par domaine si l'assemblage diffère de la prédiction, puis fusionnez."],
        "next": ["phenix_real_space_refine", "merge_models"],
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
            "Relisez methods_draft.md et table1_draft.md : les cases de collecte de données restent à remplir.",
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
        "avoid": ["Opérations déjà couvertes par un job dédié (meilleure traçabilité et rapports)."],
        "inputs": "Les entrées utiles au programme ; choisissez l'environnement (configuration d'un outil).",
        "tips": ["Indiquez les motifs de sortie (ex. result*.mrc) pour récupérer cartes et modèles."],
        "next": [],
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
    "Map processing": "Traitement de carte",
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
