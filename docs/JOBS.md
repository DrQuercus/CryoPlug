# Référence des jobs CryoPlug

Fichier généré par `cryoplug docs-jobs` à partir de `cryoplug/jobhelp.py` — ne pas éditer à la main.
Les mêmes fiches sont affichées dans l'interface (constructeur de job, détails, page d'aide).

- [Import](#import)
- [Traitement de carte](#traitement-de-carte)
- [Hétérogénéité (variabilité 3D)](#hétérogénéité-variabilité-3d)
- [Construction de modèle](#construction-de-modèle)
- [Reconstruction interactive](#reconstruction-interactive)
- [Affinement](#affinement)
- [Validation](#validation)
- [Dépôt](#dépôt)
- [Utilitaires](#utilitaires)

## Import

### Import atomic model

`import_model` — intégré (aucun logiciel externe)

Importe un modèle atomique : fichier PDB/mmCIF, entrée de la PDB ou prédiction de l'AlphaFold Database.

**Quand l'utiliser**

- Structure homologue ou ancienne structure à recaler dans la nouvelle carte.
- Prédiction AlphaFold pour un docking (résolution trop basse pour une construction de novo).
- Modèle final à valider ou à déposer.

**À éviter / pièges**

- Affiner directement un modèle AlphaFold : passez d'abord par « Process predicted model » puis un ajustement dans la carte.

**Entrées conseillées** : Fichier PDB/mmCIF, code PDB (4 caractères) ou accession UniProt (AlphaFold DB).

**Conseils**

- Une colonne B-factor de type pLDDT est détectée et signalée.
- La sortie « sequence » est extraite du modèle : pratique, mais ne remplace pas la séquence de l'échantillon pour la vérification avant dépôt.

**Étapes suivantes** : Process predicted model (Phenix), Rigid-body fit (ChimeraX), Dock in map (Phenix), Map-model validation (Q-score, FSC)

### Import from CryoSPARC

`import_cryosparc` — intégré (aucun logiciel externe)

Point d'entrée habituel : récupère la reconstruction finale d'un job CryoSPARC (raffinement homogène, non-uniforme ou local, Sharpening Tools, Local Filtering) et recalcule la FSC gold-standard.

**Quand l'utiliser**

- Votre meilleure carte vient de CryoSPARC.
- Vous avez besoin des demi-cartes non filtrées (LocScale, spIsoNet, Servalcat, FSC, dépôt EMDB).
- Vous voulez que la résolution soit ensuite remplie automatiquement (« auto ») dans tous les jobs.

**À éviter / pièges**

- Carte produite par un autre logiciel (RELION, cisTEM, EMDB…) : utilisez « Import maps ».
- Job de classification 3D ou carte intermédiaire : importez le raffinement final.

**Entrées conseillées** : Le dossier du job CryoSPARC (ex. /data/CS-projet/J245). La dernière itération est choisie automatiquement : vérifiez la liste dans le tableau « Imported files ».

**Conseils**

- Laissez « Compute half-map FSC » activé : la résolution corrigée est propagée aux jobs suivants.
- Indiquez la symétrie réellement imposée (C1, C2, D7…) : LocScale, EMmerNet et le dépôt l'utilisent.
- « copy » rend le projet autonome ; « symlink » économise de l'espace mais dépend du dossier CryoSPARC.

**Étapes suivantes** : Directional resolution (3D FSC), LocScale 2, ModelAngelo build, CryoAtom2 build, Import sequence

### Import maps

`import_maps` — intégré (aucun logiciel externe)

Importe des demi-cartes, une carte complète, une carte affûtée et un masque venant de n'importe quel logiciel.

**Quand l'utiliser**

- Reconstruction RELION, cisTEM, cryoDRGN, ou carte téléchargée de l'EMDB.
- La taille de pixel de l'en-tête est fausse (calibration du grossissement) : option de correction.
- Valider ou déposer un modèle existant (workflow « Validate & prepare deposition »).

**À éviter / pièges**

- Job CryoSPARC : « Import from CryoSPARC » trouve les fichiers tout seul.

**Entrées conseillées** : Les deux demi-cartes non filtrées sont le plus utile ; la carte affûtée sert à la construction et au dépôt.

**Conseils**

- Sans demi-cartes, pas de FSC, ni LocScale/spIsoNet/Servalcat optimaux.
- Une correction de taille de pixel s'applique aux copies importées (mode copy obligatoire).

**Étapes suivantes** : Half-map FSC, Directional resolution (3D FSC), LocScale 2, Import atomic model

### Import particles

`import_particles` — intégré (aucun logiciel externe)

Importe des particules pour l'analyse d'hétérogénéité (cryoDRGN) : un job CryoSPARC ou un fichier .cs (fusionné avec son fichier « passthrough »), ou un fichier .star de RELION. Seules les métadonnées sont lues : les images restent où elles sont.

**Quand l'utiliser**

- Les particules ne viennent pas d'un job déjà importé avec « Import from CryoSPARC ».
- Particules exportées d'un autre projet, ou d'un autre logiciel (.star).

**À éviter / pièges**

- Particules sans poses (extraction, classification 2D) : cryoDRGN a besoin d'un raffinement consensus.

**Entrées conseillées** : Le dossier du job de raffinement consensus (ses particules portent les poses), un .cs ou un .star.

**Conseils**

- « Import from CryoSPARC » importe déjà les particules du job en même temps que les cartes.
- Si les chemins des images sont cassés, indiquez le dossier des images (Image folder).

**Étapes suivantes** : cryoDRGN training

### Import sequence

`import_sequence` — intégré (aucun logiciel externe)

Déclare la ou les séquences de l'échantillon (FASTA collé, fichier ou accessions UniProt). Les enregistrements protéine, ARN et ADN sont séparés automatiquement.

**Quand l'utiliser**

- Dès que la composition est connue : elle guide ModelAngelo, CryoAtom2, Boltz-2 et ColabFold.
- Pour la vérification séquence/modèle de la checklist avant dépôt.

**À éviter / pièges**

- Répéter une séquence pour chaque copie d'un homo-oligomère : une seule entrée par entité distincte.

**Entrées conseillées** : Un enregistrement FASTA par entité (chaîne différente).

**Conseils**

- Incluez les étiquettes (tags) réellement présentes si elles sont visibles dans la carte.
- Les ARN/ADN sont reconnus à leur composition (A, C, G, U/T).

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, Complex prediction (Boltz-2), AlphaFold2 prediction (ColabFold)

### Import volume series

`import_volume_series` — intégré (aucun logiciel externe)

Importe des séries de volumes : 3D Variability Display et 3D Flex Generate de CryoSPARC, cryoDRGN, multi-body de RELION… depuis un dossier, un ZIP ou un motif de fichiers. Une série par composante.

**Quand l'utiliser**

- Vous avez lancé la 3D Variability (3DVA) ou 3D Flex dans CryoSPARC et voulez interpréter les mouvements ici.
- Comparer des volumes de différents états produits ailleurs.

**À éviter / pièges**

- Volumes de boîtes ou de tailles de pixel différentes dans une même série.

**Entrées conseillées** : Le dossier du job 3D Variability Display (ou le ZIP téléchargé d'une composante).

**Conseils**

- Les fichiers sont regroupés par « component N » dans leur nom, puis triés par numéro de frame.
- Les coordonnées de chaque particule le long des composantes s'importent avec le job 3D Variability (Import from CryoSPARC) : elles donnent un espace latent.
- Bouton 3D : la série se joue comme un film dans le visualiseur.

**Étapes suivantes** : Volume series analysis, Extract volume from series

## Traitement de carte

### Anisotropy correction (spIsoNet)

`spisonet` — logiciel : `spisonet` · GPU

Corrige l'anisotropie due à l'orientation préférentielle : un réseau auto-supervisé apprend, à partir des demi-cartes et de la 3D FSC, à restaurer les directions mal échantillonnées.

**Quand l'utiliser**

- Anisotropie marquée (rapport > 1,5 dans « Directional resolution ») ou densité visiblement étirée.
- Impossible de recollecter avec inclinaison ou de rééquilibrer les vues.

**À éviter / pièges**

- Anisotropie faible : peu de gain, temps de calcul élevé.
- Utiliser les demi-cartes corrigées pour la FSC rapportée ou pour Servalcat : elles ne sont plus indépendantes.

**Entrées conseillées** : Demi-cartes non filtrées et non masquées + masque doux (obligatoire).

**Conseils**

- Comptez des dizaines de minutes sur GPU.
- Déposez toujours les cartes d'origine ; la carte corrigée peut être déposée en carte additionnelle et mentionnée dans les méthodes.
- Jugez le résultat visuellement et par l'ajustement du modèle (Q-score), pas par une nouvelle FSC.

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, ISOLDE session

### Auto-sharpen (Phenix)

`phenix_auto_sharpen` — logiciel : `phenix`

Affûtage global optimisé pour maximiser le détail et la connectivité (phenix.auto_sharpen).

**Quand l'utiliser**

- La carte affûtée de CryoSPARC semble sur- ou sous-affûtée.
- Vous partez d'une carte non affûtée d'un autre logiciel.

**À éviter / pièges**

- Résolution locale très hétérogène : préférez LocScale ou l'affûtage local anisotrope.

**Entrées conseillées** : La carte NON affûtée ; les demi-cartes sont optionnelles.

**Conseils**

- Comparez visuellement avec la carte CryoSPARC dans le visualiseur 3D avant de choisir.

**Étapes suivantes** : ModelAngelo build, Rigid-body fit (ChimeraX)

### Composite map (focused maps)

`composite_map` — logiciel : `phenix`

Assemble les cartes des raffinements locaux (focalisés) en une carte composite (phenix.combine_focused_maps) : chaque partie du modèle prend la carte où il s'ajuste le mieux. Les demi-cartes de chaque carte sont combinées de la même façon quand elles existent.

**Quand l'utiliser**

- Gros complexe résolu par plusieurs raffinements locaux dans CryoSPARC (une carte nette par région).
- Construire et affiner un modèle unique dans une seule carte au lieu de jongler entre les cartes locales.
- Préparer le dépôt : l'EMDB attend la carte composite avec les cartes locales et la carte consensus.

**À éviter / pièges**

- Rapporter la FSC de la carte composite comme une FSC gold-standard : donnez celles des cartes locales.
- Cartes dont les boîtes ou les tailles de pixel diffèrent beaucoup : vérifiez-les avant.

**Entrées conseillées** : Le modèle ajusté dans la carte consensus, la carte consensus (référence) et jusqu'à 8 cartes locales (par exemple un import CryoSPARC par raffinement local).

**Conseils**

- Les demi-cartes sont retrouvées automatiquement à côté de chaque carte (même job) : il faut les avoir pour toutes.
- Les demi-cartes composites permettent ensuite la FSC et la résolution locale de la carte composite.
- Un emplacement « Focused map » de plus apparaît dès que le précédent est rempli.

**Étapes suivantes** : Local resolution, Real-space refinement (Phenix), ISOLDE session

### Create mask

`create_mask` — intégré (aucun logiciel externe)

Crée un masque à bord doux : à partir de la densité (passe-bas + seuil), des atomes de chaînes choisies, ou de la densité proche de ces atomes. Dilatation et bord doux en Å, comme dans CryoSPARC ; combinaison avec un autre masque (union, soustraction, intersection).

**Quand l'utiliser**

- Masque de raffinement local d'une sous-partie d'un gros complexe (chaînes choisies + densité voisine).
- Masque FSC, LocScale ou spIsoNet quand celui de CryoSPARC manque ou ne convient pas.
- Soustraction de signal : masque du complexe moins la région à garder (« subtract »).
- Masque à fournir à l'EMDB avec la carte déposée.

**À éviter / pièges**

- Masque trop serré (dilatation + bord < 6 Å) pour la FSC : il gonfle la résolution.
- Seuil automatique sans masse attendue sur une carte très bruitée : vérifiez la valeur et les coupes.

**Entrées conseillées** : Une carte (elle fixe la boîte et le pixel) ; un modèle ajusté pour les masques par chaînes ; un second masque pour les combinaisons.

**Conseils**

- Raffinement local : densité proche du modèle (6–8 Å), dilatation 3–5 Å, bord 8–12 Å.
- FSC / LocScale : densité filtrée à 15 Å, dilatation 3 Å, bord 6 Å.
- Donnez la masse attendue (kDa) pour un seuil automatique fiable ; sinon la masse des atomes choisis est utilisée.
- Les coupes du rapport montrent le masque (orange) sur la carte : vérifiez qu'il ne coupe pas de densité.
- Le chemin du fichier est donné pour l'importer dans CryoSPARC (Import 3D Volumes, type mask).

**Étapes suivantes** : Half-map FSC, LocScale 2, Anisotropy correction (spIsoNet), Local resolution

### DeepEMhancer

`deepemhancer` — logiciel : `deepemhancer` · GPU

Post-traitement par apprentissage profond : masquage et affûtage local implicites.

**Quand l'utiliser**

- Cartes de résolution modérée (≈ 3,5–6 Å) pour visualiser, faire des figures, ajuster des domaines.
- Région flexible ou micelle gênante pour la lecture de la carte.

**À éviter / pièges**

- Construction automatique : ModelAngelo fonctionne moins bien sur des cartes DeepEMhancer (recommandation des auteurs).
- Carte primaire du dépôt, validation.

**Entrées conseillées** : Demi-cartes non filtrées (préférées) ou carte complète.

**Conseils**

- tightTarget par défaut ; wideTarget conserve plus de densité autour de la particule ; highRes pour les cartes meilleures que 4 Å.

**Étapes suivantes** : Rigid-body fit (ChimeraX), ISOLDE session

### Density modification (Phenix)

`phenix_resolve_cryo_em` — logiciel : `phenix`

Modification de densité au maximum de vraisemblance à partir des demi-cartes (phenix.resolve_cryo_em).

**Quand l'utiliser**

- Améliorer l'interprétabilité, souvent aussi la FSC demi-cartes, surtout entre 3 et 5 Å.
- Avant une construction automatique à résolution moyenne.

**À éviter / pièges**

- Comme carte primaire du dépôt (déposez-la en carte additionnelle).

**Entrées conseillées** : Demi-cartes non filtrées ; la séquence améliore le résultat (sinon donner la teneur en solvant).

**Conseils**

- Augmentez nproc : le calcul est long sur de grandes boîtes.

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, Real-space refinement (Phenix)

### Directional resolution (3D FSC)

`directional_fsc` — intégré (aucun logiciel externe)

Résolution dans chaque direction (FSC coniques, « 3D FSC ») pour mesurer l'anisotropie causée par l'orientation préférentielle des particules.

**Quand l'utiliser**

- La distribution angulaire de CryoSPARC montre des vues dominantes.
- La densité paraît étirée selon un axe, ou les hélices sont nettes dans un plan et floues dans un autre.
- Avant de décider d'un spIsoNet ou d'une nouvelle collecte inclinée (tilt).

**À éviter / pièges**

- Très petites particules : chaque cône contient peu de coefficients, le résultat est bruité.
- Ce n'est pas une résolution locale (par région) mais par direction.

**Entrées conseillées** : Demi-cartes non filtrées et masque FSC.

**Conseils**

- Rapport pire/meilleure direction ≤ 1,25 : isotrope ; 1,25–1,6 : modéré ; > 1,6 : anisotropie forte.
- Élévation 90° = axe z de la carte ; directions opposées équivalentes (hémisphère).

**Étapes suivantes** : Anisotropy correction (spIsoNet), LocScale 2

### EMReady

`emready` — logiciel : `emready` · GPU

Amélioration de carte par apprentissage profond combinant informations locales et non locales.

**Quand l'utiliser**

- Alternative à DeepEMhancer/EMmerNet pour l'interprétation, notamment le tracé de la chaîne principale.

**À éviter / pièges**

- Mêmes limites que les autres réseaux : interprétation seulement, pas de validation ni de dépôt comme carte primaire.

**Entrées conseillées** : La carte (non affûtée ou affûtée), masque optionnel.

**Conseils**

- Un pas (stride) plus petit est plus précis mais plus lent.

**Étapes suivantes** : Rigid-body fit (ChimeraX), ModelAngelo build

### EMmerNet feature enhancement

`emmernet` — logiciel : `locscale` · GPU

Carte « améliorée » par le réseau EMmerNet (LocScale 2) avec estimation d'incertitude Monte-Carlo.

**Quand l'utiliser**

- Aide à l'interprétation : connectivité de la chaîne, chaînes latérales, régions difficiles.
- Comparer avec la carte LocScale ou affûtée pour lever un doute.

**À éviter / pièges**

- Seule preuve d'un détail structural : un réseau peut « halluciner » de la densité.
- Validation ou carte primaire du dépôt.

**Entrées conseillées** : Demi-cartes non filtrées (ou carte complète non affûtée).

**Conseils**

- Regardez la carte de confiance/incertitude produite : les zones peu fiables doivent être interprétées avec prudence.

**Étapes suivantes** : ModelAngelo build, ISOLDE session

### Half-map FSC

`map_fsc` — intégré (aucun logiciel externe)

FSC gold-standard entre demi-cartes : sans masque, avec masque et corrigée par randomisation de phase (Chen et al. 2013). Écrit la courbe au format XML de l'EMDB.

**Quand l'utiliser**

- Vérifier ou recalculer la résolution annoncée.
- Après avoir changé de masque.
- Obtenir le fichier FSC XML pour le dépôt.

**À éviter / pièges**

- Demi-cartes qui ne sont plus indépendantes (après spIsoNet ou un traitement commun) : la FSC serait surestimée.

**Entrées conseillées** : Demi-cartes non filtrées et, idéalement, le masque FSC (doux).

**Conseils**

- C'est la courbe « corrigée » qu'on rapporte (seuil 0,143).
- Un masque trop serré fait remonter la courbe masquée à haute résolution : la correction le compense en partie.

**Étapes suivantes** : Directional resolution (3D FSC), Deposition package

### LocScale 2

`locscale` — logiciel : `locscale` · GPU

Affûtage local (local amplitude scaling) : renforce le contraste là où la carte est moins résolue sans sur-affûter les régions bien résolues. Modes model-free (référence EMmerNet), pseudo-modèle, model-based et hybride.

**Quand l'utiliser**

- Résolution locale très variable : cœur net, périphérie, domaines mobiles ou micelle flous.
- La carte affûtée globalement est trop bruitée en périphérie ou trop lissée au cœur.
- Une fois un premier modèle disponible : mode model-based (modèle complet) ou hybride (modèle partiel) pour la carte d'interprétation finale.

**À éviter / pièges**

- Demi-cartes déjà filtrées ou affûtées : l'information perdue ne se récupère pas.
- Mode model-based avec un modèle incomplet : préférez le mode hybride.
- Déposer la carte LocScale à la place de la carte primaire (elle peut être déposée en carte additionnelle).

**Entrées conseillées** : Demi-cartes non filtrées de CryoSPARC (préférées), masque, et un modèle pour les modes model-based/hybride.

**Conseils**

- Renseignez la symétrie pour une référence symétrisée.
- Le mode model-free utilise EMmerNet : un GPU est fortement conseillé.
- Le mode pseudo-modèle convient aux cartes très bruitées ou de basse résolution.

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, Rigid-body fit (ChimeraX), ISOLDE session

### Local anisotropic sharpening (Phenix)

`phenix_local_aniso_sharpen` — logiciel : `phenix`

Affûtage local et anisotrope avec mise à l'échelle dépendant de la résolution, à partir des demi-cartes (éventuellement guidé par un modèle).

**Quand l'utiliser**

- Résolution variable avec une légère anisotropie ; alternative Phenix à LocScale.

**À éviter / pièges**

- Anisotropie forte : envisagez spIsoNet.

**Entrées conseillées** : Demi-cartes non filtrées, modèle optionnel.

**Conseils**

- Avec un modèle, le résultat suit mieux la résolution locale réelle.

**Étapes suivantes** : ModelAngelo build, Real-space refinement (Phenix)

### Local resolution

`local_resolution` — logiciel : `phenix`

Carte de résolution locale à partir des deux demi-cartes (phenix.local_resolution) : distribution dans la molécule et, avec un modèle, résolution de chaque chaîne et de chaque résidu. Le visualiseur 3D colorie une carte avec.

**Quand l'utiliser**

- Gros complexe : savoir quelles sous-unités sont bien résolues et lesquelles demandent un raffinement local.
- Avant de construire ou d'interpréter des chaînes latérales dans une région périphérique.
- Figure de résolution locale pour l'article et l'EMDB.
- Après une carte composite (avec ses demi-cartes composites).

**À éviter / pièges**

- Demi-cartes non indépendantes (après spIsoNet, LocScale ou un débruitage commun) : la résolution serait surestimée.
- Demi-cartes masquées ou filtrées : donnez les demi-cartes brutes.

**Entrées conseillées** : Demi-cartes non filtrées ; la carte à colorier (affûtée), le masque FSC et le modèle sont facultatifs.

**Conseils**

- Le tableau par chaîne classe les sous-unités de la mieux à la moins bien résolue.
- model_local_resolution.cif porte la résolution locale dans la colonne B-factor (coloriage dans ChimeraX, Coot, PyMOL).
- Dans le visualiseur : menu Colour de la carte → Local resolution ; l'échelle va du 5e au 95e centile.
- Une carte de résolution locale importée de CryoSPARC s'utilise de la même façon.

**Étapes suivantes** : Create mask, LocScale 2, Map-model validation (Q-score, FSC)

### Map operations

`map_tools` — intégré (aucun logiciel externe)

Opérations simples sur une carte : taille de pixel, recadrage/agrandissement de boîte, inversion de main, B-factor global, filtre passe-bas, multiplication par un masque, normalisation.

**Quand l'utiliser**

- La main est fausse (hélices gauches, ModelAngelo ne construit que des fragments) : « Flip handedness ».
- Boîte non cubique ou trop grande pour un logiciel : recadrer en boîte cubique.
- Carte filtrée à une résolution donnée pour une figure ou un ajustement grossier.

**À éviter / pièges**

- Recadrer la carte déposée sans recadrer de même les demi-cartes et le masque.

**Entrées conseillées** : Une carte ; le masque seulement pour « Multiply by mask ».

**Conseils**

- Les opérations s'enchaînent dans un ordre fixe : pixel → boîte → main → B-factor → filtre → masque → normalisation.
- B-factor négatif = affûtage, positif = flou.

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, Rigid-body fit (ChimeraX)

## Hétérogénéité (variabilité 3D)

### Extract volume from series

`extract_volume` — intégré (aucun logiciel externe)

Prend un volume d'une série (un cluster cryoDRGN, une frame de 3DVA) comme carte, pour construire ou ajuster un modèle de cet état.

**Quand l'utiliser**

- Ajuster le modèle dans un état particulier (Rigid-body fit, ISOLDE) ou comparer des états.

**À éviter / pièges**

- Affiner finement un modèle dans un volume cryoDRGN basse résolution : raffinez plutôt les particules de cet état dans CryoSPARC.

**Entrées conseillées** : Une série de volumes et le numéro du volume (comme dans le visualiseur).

**Conseils**

- Depuis l'explorateur latent : un cluster sélectionné → « Extract map… » prépare ce job avec le bon numéro.
- Indiquez la résolution dans les jobs suivants : elle n'est pas connue pour ces volumes.

**Étapes suivantes** : Rigid-body fit (ChimeraX), ISOLDE session, Map-model validation (Q-score, FSC)

### Select particles (latent clusters)

`select_particles` — intégré (aucun logiciel externe)

Garde ou retire les particules de clusters latents choisis (cryoDRGN ou 3D variability) : pour éliminer le « junk » ou isoler un état. Écrit un fichier .cs pour CryoSPARC et des indices pour cryoDRGN.

**Quand l'utiliser**

- Des clusters ont des volumes aberrants (junk, particules cassées) : retirez-les puis réentraînez.
- Un état intéressant : gardez ses particules et raffinez-les dans CryoSPARC pour une carte à haute résolution.

**À éviter / pièges**

- Sélectionner sur un modèle non convergé : les clusters peuvent changer.

**Entrées conseillées** : Un espace latent avec ses clusters (numéros affichés dans l'explorateur).

**Conseils**

- Le plus simple : dans l'explorateur, cliquez les clusters puis « Keep… » ou « Remove… » (le job est préparé avec leurs numéros).
- Un nouvel entraînement cryoDRGN sur la sélection réutilise les images déjà réduites (pas de nouveau sous-échantillonnage).
- Dans CryoSPARC : Import Particle Stack avec le fichier .cs produit, puis Homogeneous / NU refinement.

**Étapes suivantes** : cryoDRGN training

### Volume series analysis

`series_analysis` — intégré (aucun logiciel externe)

Interprète une série de volumes (frames de 3D variability, clusters ou trajectoire cryoDRGN) : carte de variabilité (où la densité change), cartes moyenne et différence, similarité entre frames et, avec un modèle, les chaînes qui bougent ou qui apparaissent et disparaissent.

**Quand l'utiliser**

- Après cryoDRGN ou la 3DVA : savoir quelles régions varient et si c'est un mouvement ou une occupation partielle.
- Gros complexe : identifier les sous-unités flexibles ou absentes d'une partie des particules.

**À éviter / pièges**

- Séries de volumes non superposés (boîtes ou origines différentes).

**Entrées conseillées** : Une série de volumes ; le modèle (ajusté dans ces volumes) et un masque sont facultatifs.

**Conseils**

- View 3D colore la carte moyenne par la variabilité (bleu = stable, rouge = variable) ; le menu Colour applique la même coloration à n'importe quelle carte ou série superposée.
- Matrice de corrélation : des blocs = des états distincts ; un dégradé le long de la diagonale = un mouvement continu.
- Tableau par chaîne : « fades in some frames » = la chaîne perd sa densité dans certains volumes : absente d'une partie des particules, ou déplacée hors de sa place dans le modèle (regardez la série pour trancher).

**Étapes suivantes** : Extract volume from series, Select particles (latent clusters)

### cryoDRGN analysis

`cryodrgn_analyze` — logiciel : `cryodrgn` · GPU

Analyse à nouveau un modèle cryoDRGN entraîné : autre époque, plus de clusters, trajectoires plus longues, main inversée… Espace latent, volumes des clusters et trajectoires le long des composantes principales.

**Quand l'utiliser**

- Échantillonner plus finement l'espace latent (k plus grand) pour voir des états rares.
- Comparer deux époques pour juger la convergence.

**À éviter / pièges**

- Relancer un entraînement complet pour seulement changer le nombre de volumes.

**Entrées conseillées** : L'espace latent d'un entraînement cryoDRGN.

**Conseils**

- Epoch = 0 reprend l'époque de l'entrée (la dernière de l'entraînement).

**Étapes suivantes** : Volume series analysis, Select particles (latent clusters), cryoDRGN trajectory

### cryoDRGN training

`cryodrgn_train` — logiciel : `cryodrgn` · GPU

Reconstruction hétérogène avec cryoDRGN : un réseau de neurones apprend un espace latent des conformations et compositions présentes dans les particules (poses du raffinement consensus), puis génère des volumes dans tout cet espace. Résultats : explorateur interactif de l'espace latent, volumes de chaque cluster et trajectoires le long des composantes principales.

**Quand l'utiliser**

- La carte consensus a des régions floues qui pourraient bouger ou être partiellement occupées.
- Chercher des états minoritaires, des sous-unités absentes d'une partie des particules, des mouvements continus.
- Nettoyer les particules : repérer les clusters de « junk » et les retirer.

**À éviter / pièges**

- Particules sans poses fiables : faites d'abord un bon raffinement consensus (NU-refine).
- Lire les volumes cryoDRGN comme des cartes haute résolution : ils servent à voir les états, pas à affiner un modèle fin.

**Entrées conseillées** : Les particules d'un raffinement consensus (Import from CryoSPARC les importe avec les cartes).

**Conseils**

- Premier passage à 128 px et 25 époques, z = 8 ; passage final à 256 px après nettoyage des particules.
- Vérifiez la convergence : les courbes de perte doivent se stabiliser ; refaire à 50 époques ne doit pas changer les états.
- Les images réduites sont gardées en sortie (particles_prepared) et réutilisées par un nouvel entraînement à la même taille.
- Plusieurs GPU (--multigpu) surtout utiles à 256 px.
- Explorateur : cliquez des clusters (sur le nuage ou leurs pastilles) pour jouer leurs volumes en 3D, les garder ou les retirer (Keep… / Remove… préparent le job de sélection), ou suivre la transition (Trajectory…) ; double-clic = ouvrir le volume d'un cluster.

**Étapes suivantes** : Volume series analysis, Select particles (latent clusters), cryoDRGN trajectory, cryoDRGN analysis

### cryoDRGN trajectory

`cryodrgn_trajectory` — logiciel : `cryodrgn` · GPU

Génère les volumes le long d'un chemin de l'espace latent entre des clusters choisis (à travers les particules, ou en ligne droite) : un film de la transition à jouer dans le visualiseur.

**Quand l'utiliser**

- Visualiser le passage d'un état à un autre repéré dans l'explorateur latent.
- Préparer une figure ou une vidéo de mouvement pour l'article.

**À éviter / pièges**

- Interpréter un chemin en ligne droite qui traverse des régions vides de particules : préférez le chemin à travers les particules.

**Entrées conseillées** : L'espace latent d'un entraînement ou d'une analyse cryoDRGN, et les numéros de clusters à relier.

**Conseils**

- Dans l'explorateur, cliquez les clusters dans l'ordre du chemin puis « Trajectory… » : le job est préparé.
- Le chemin est tracé sur l'explorateur latent du rapport.
- Volume series analysis sur la trajectoire montre où la densité change.

**Étapes suivantes** : Volume series analysis, Extract volume from series

## Construction de modèle

### AlphaFold2 prediction (ColabFold)

`colabfold_predict` — logiciel : `colabfold` · GPU

Prédiction AlphaFold2 locale (ColabFold) pour obtenir des modèles de départ.

**Quand l'utiliser**

- Résolution insuffisante pour une construction de novo (≈ 4–8 Å).
- Modèle de départ d'un domaine ou d'une sous-unité, test d'une hypothèse d'assemblage.

**À éviter / pièges**

- Régions désordonnées (pLDDT bas) ou conformation différente de celle capturée dans la carte.
- Complexes avec ligands ou acides nucléiques : préférez Boltz-2.

**Entrées conseillées** : Séquence(s) protéiques ; « Predict as a complex » pour prédire l'assemblage.

**Conseils**

- Passez ensuite par « Process predicted model » puis un ajustement dans la carte.

**Étapes suivantes** : Process predicted model (Phenix), Rigid-body fit (ChimeraX)

### Complex prediction (Boltz-2)

`boltz_predict` — logiciel : `boltz` · GPU

Prédiction de structure de complexes entiers (protéines, ARN/ADN, ligands par code CCD ou SMILES) avec Boltz-2.

**Quand l'utiliser**

- Complexe contenant des acides nucléiques, des cofacteurs ou un ligand/médicament.
- Modèle de départ pour un docking à 4–8 Å.
- Proposer une pose de ligand avant de l'ajuster dans la densité.

**À éviter / pièges**

- Séquences confidentielles avec le serveur MSA activé (les séquences partent vers api.colabfold.com) : désactivez-le.
- Considérer la pose prédite d'un ligand comme validée sans densité qui la soutient.

**Entrées conseillées** : Séquences (une par entité) ; nombre de copies ; ligands (CCD:ATP, 2xCCD:MG, SMILES:…).

**Conseils**

- ipTM > 0,8 : interface prédite avec confiance ; < 0,6 : à prendre avec précaution.
- Plusieurs échantillons (samples) donnent une idée de la variabilité.

**Étapes suivantes** : Process predicted model (Phenix), Rigid-body fit (ChimeraX), Dock in map (Phenix)

### CryoAtom2 build

`cryoatom_build` — logiciel : `cryoatom` · GPU

Construction automatique (CryoAtom2 : attention locale et encodage de position 3D) de protéines, ARN/ADN et complexes, avec identification de séquences intégrée et construction locale par masque.

**Quand l'utiliser**

- Comme ModelAngelo ; souvent plus complet à résolution moyenne.
- Complexes protéine–acide nucléique.
- Identification de chaînes avec une base de séquences (protéines et acides nucléiques).
- Reconstruire une seule région de la carte (masque) ou identifier une chaîne tracée à la main (backbone Cα/P).

**À éviter / pièges**

- GPU de moins de ~14 Go.
- Résolution > 4,5 Å, main inversée (mêmes limites que ModelAngelo).

**Entrées conseillées** : Carte affûtée ou LocScale, séquence(s) ; base de séquences pour l'identification ; masque/backbone optionnels.

**Conseils**

- Itérez : identification → reconstruction avec la sortie « Identified sequences », jusqu'à ce qu'aucune nouvelle séquence n'apparaisse.
- Un premier lancement télécharge les poids ESM et RNA-FM (~3 Go).
- Comparez avec ModelAngelo (Q-score, résidus construits).

**Étapes suivantes** : Real-space refinement (Phenix), ISOLDE session, Map-model validation (Q-score, FSC)

### Dock in map (Phenix)

`phenix_dock_in_map` — logiciel : `phenix`

Recherche globale de la position d'un modèle (ou d'un domaine) dans la carte (phenix.dock_in_map).

**Quand l'utiliser**

- Position inconnue, plusieurs copies possibles, carte de résolution moyenne à basse.

**À éviter / pièges**

- Modèle déjà presque en place : un ajustement local (ChimeraX fitmap) suffit et va plus vite.

**Entrées conseillées** : Modèle (traité s'il est prédit) et carte.

**Conseils**

- Dockez domaine par domaine si l'assemblage diffère de la prédiction, puis fusionnez.

**Étapes suivantes** : Real-space refinement (Phenix), Merge models

### Identify chains (ModelAngelo HMM search)

`modelangelo_hmm_search` — logiciel : `modelangelo`

Identifie les protéines (ou ARN/ADN) présentes dans la carte : les profils HMM d'un ModelAngelo sans séquence sont recherchés dans une base de séquences (HMMER).

**Quand l'utiliser**

- Sous-unité inconnue, contaminant, partenaire co-purifié, densité inattendue.
- Échantillon natif (purification endogène) dont la composition exacte est incertaine.

**À éviter / pièges**

- Modèle construit AVEC séquence : il n'y a pas de profils HMM.
- Base inutilement énorme (UniProt complet) : plus lent et plus de faux positifs ; préférez le protéome de l'organisme.

**Entrées conseillées** : Le modèle d'un « ModelAngelo build » lancé sans séquence + un FASTA (ex. protéome de référence UniProt décompressé).

**Conseils**

- Les hits sous le seuil d'E-value (1e-5 par défaut) deviennent une sortie « sequence ».
- Reconstruisez ensuite avec ces séquences (ModelAngelo ou CryoAtom2) et vérifiez l'accord de séquence dans la carte.

**Étapes suivantes** : CryoAtom2 build, ModelAngelo build

### Ligand restraints (eLBOW)

`phenix_elbow` — logiciel : `phenix`

Génère les restreintes géométriques (CIF) et des coordonnées d'un ligand à partir de son code CCD ou d'un SMILES (eLBOW).

**Quand l'utiliser**

- Ligand, cofacteur ou médicament absent de la bibliothèque de restreintes de l'outil d'affinement.
- Nouveau composé décrit seulement par un SMILES.

**À éviter / pièges**

- Ions, eaux, acides aminés et nucléotides standards : déjà connus.

**Entrées conseillées** : Code CCD (ATP, NAG…) ou SMILES + nom de résidu.

**Conseils**

- Utilisez le code CCD officiel quand le ligand existe : le dépôt est plus simple.
- Branchez la sortie « restraints » sur Real-space refinement ou Servalcat ; fusionnez les coordonnées avec « Merge models » si besoin.

**Étapes suivantes** : Real-space refinement (Phenix), Refinement (Servalcat), Merge models

### ModelAngelo build

`modelangelo_build` — logiciel : `modelangelo` · GPU

Construction automatique de novo (réseaux de graphes) de protéines, ARN et ADN, guidée par la séquence. Sans séquence : construction « build_no_seq » et profils HMM pour identifier les chaînes.

**Quand l'utiliser**

- Résolution meilleure qu'environ 4 Å (idéalement < 3,5 Å) et séquence connue.
- Grands complexes : construit des milliers de résidus en quelques dizaines de minutes.
- Sans séquence : sous-unités inconnues (puis « Identify chains »).

**À éviter / pièges**

- Résolution > 4,5 Å : préférez le docking de modèles prédits (AlphaFold, Boltz-2).
- Carte DeepEMhancer, carte non cubique (risque de décalage : recadrez en boîte cubique), main inversée (hélices fragmentées).

**Entrées conseillées** : La meilleure carte affûtée (CryoSPARC, LocScale ou modification de densité) et la séquence.

**Conseils**

- Une entrée FASTA par entité, sans répétition des copies.
- La sortie « raw » montre tout ce qui a été construit, y compris les régions mal attribuées.
- Comparez avec CryoAtom2 et gardez le meilleur selon le Q-score et la complétude.

**Étapes suivantes** : Real-space refinement (Phenix), ISOLDE session, Map-model validation (Q-score, FSC), Identify chains (ModelAngelo HMM search)

### Process predicted model (Phenix)

`phenix_process_predicted_model` — logiciel : `phenix`

Prépare un modèle prédit : retire les résidus de faible confiance (pLDDT), convertit le pLDDT en B-factors, découpe éventuellement en domaines compacts.

**Quand l'utiliser**

- Toujours avant d'ajuster ou d'affiner un modèle AlphaFold/ColabFold/Boltz.
- Domaines mobiles : découpe pour les ajuster séparément.

**À éviter / pièges**

- Modèle expérimental : ses B-factors ne sont pas des pLDDT.

**Entrées conseillées** : Le modèle prédit.

**Conseils**

- pLDDT minimal 70 par défaut ; baissez-le si vous perdez des régions importantes bien définies dans la carte.

**Étapes suivantes** : Rigid-body fit (ChimeraX), Dock in map (Phenix)

### Rigid-body fit (ChimeraX)

`chimerax_fitmap` — logiciel : `chimerax`

Ajustement de corps rigide par corrélation avec une carte simulée à la résolution de la carte (ChimeraX fitmap).

**Quand l'utiliser**

- Affiner la position d'un modèle déjà placé (optimisation locale).
- Recherche globale d'un domaine (paramètre « Global search placements » > 0).

**À éviter / pièges**

- Changement de conformation important : il faut une flexibilité (ISOLDE, real-space refine avec morphing).

**Entrées conseillées** : Modèle et carte (affûtée ou filtrée).

**Conseils**

- 200 placements pour une recherche globale ; « Search radius » pour rester près de la position de départ.

**Étapes suivantes** : Real-space refinement (Phenix), ISOLDE session, Merge models

## Reconstruction interactive

### Coot session

`coot_session` — logiciel : `coot`

Construction et correction manuelles classiques dans Coot.

**Quand l'utiliser**

- Construire des boucles ou des extrémités manquantes, placer un ligand, des glycanes, des eaux.
- Parcourir les listes de problèmes MolProbity (script molprobity_coot.py).

**À éviter / pièges**

- Corrections globales d'un grand modèle : ISOLDE est souvent plus efficace.

**Entrées conseillées** : Modèle + carte (+ seconde carte).

**Conseils**

- Sauvegardez les coordonnées dans le dossier du job, puis « Finish ».

**Étapes suivantes** : Real-space refinement (Phenix)

### ISOLDE session

`isolde_session` — logiciel : `chimerax`

Reconstruction interactive par dynamique moléculaire guidée par la carte (ISOLDE dans ChimeraX).

**Quand l'utiliser**

- Corriger un modèle automatique ou docké : registre, rotamères, boucles, liaisons peptidiques cis/trans.
- Résolution 2,5–4,5 Å, là où la physique aide le plus.
- Éliminer les outliers Ramachandran/rotamères et les clashs de façon interactive.

**À éviter / pièges**

- Serveur sans affichage graphique : utilisez le paquet de session et votre poste, puis renvoyez le modèle.

**Entrées conseillées** : Modèle + carte principale (affûtée ou LocScale) + seconde carte optionnelle (non affûtée, modification de densité).

**Conseils**

- À basse résolution, utilisez des restreintes de distance adaptatives sur un modèle de référence.
- Sauvegardez dans le dossier du job (save isolde_model.cif models #1) puis « Finish » : les jobs en aval démarrent.

**Étapes suivantes** : Real-space refinement (Phenix), Refinement (Servalcat)

## Affinement

### Add waters (phenix.douse)

`phenix_douse` — logiciel : `phenix`

Place des molécules d'eau ordonnées dans la carte autour du modèle (phenix.douse).

**Quand l'utiliser**

- Cartes meilleures que ≈ 2,5–3 Å, en fin d'affinement.

**À éviter / pièges**

- Résolution > 3 Å : les eaux ne seraient pas justifiées par la densité.

**Entrées conseillées** : Modèle affiné + carte.

**Conseils**

- Inspectez les eaux ajoutées puis réaffinez le modèle complet.

**Étapes suivantes** : Real-space refinement (Phenix), Refinement (Servalcat)

### Real-space refinement (Phenix)

`phenix_real_space_refine` — logiciel : `phenix`

Affinement en espace réel avec restreintes géométriques, de structure secondaire, Ramachandran et rotamères, morphing, recuit simulé et B-factors (phenix.real_space_refine).

**Quand l'utiliser**

- Après chaque étape de construction (automatique, docking, ISOLDE, Coot).
- Affinement final court : stratégie minimization_global+adp.

**À éviter / pièges**

- Restreintes Ramachandran si vous rapportez ensuite les statistiques Ramachandran comme validation (elles perdent leur valeur).
- Carte fortement modifiée par un réseau comme cible finale : préférez la carte affûtée/primaire.

**Entrées conseillées** : Modèle + carte (en général la carte affûtée déposée) + restreintes de ligands éventuelles.

**Conseils**

- 5 macro-cycles par défaut ; morphing/recuit utiles juste après un docking.
- Les statistiques finales (MolProbity, CC_mask) sont lues dans la sortie et affichées.

**Étapes suivantes** : Map-model validation (Q-score, FSC), Comprehensive validation (Phenix), ISOLDE session, Pre-deposition checks

### Refinement (Servalcat)

`servalcat_refine` — logiciel : `servalcat`

Affinement contre les demi-cartes non affûtées avec pondération statistique, B-factors atomiques et cartes Fo-Fc (Servalcat, sans REFMAC).

**Quand l'utiliser**

- Résolution élevée (< 3 Å) et demi-cartes disponibles.
- Besoin de cartes de différence Fo-Fc pour repérer ligands, eaux, erreurs de modèle.
- Complément ou alternative à Phenix pour l'affinement final.

**À éviter / pièges**

- Demi-cartes non indépendantes (après spIsoNet).
- Avec une seule carte : possible, mais on perd l'intérêt principal.

**Entrées conseillées** : Modèle + demi-cartes non filtrées (+ masque pour Fo-Fc, restreintes de ligands).

**Conseils**

- Avec symétrie : modèle de l'unité asymétrique + point group (pg).
- Jelly-body à basse résolution ; examinez la carte Fo-Fc normalisée (pics à ±4σ).

**Étapes suivantes** : Map-model validation (Q-score, FSC), Coot session, Add waters (phenix.douse)

## Validation

### Comprehensive validation (Phenix)

`phenix_validation_cryoem` — logiciel : `phenix`

Validation complète du modèle : géométrie MolProbity et accord carte–modèle (CC_mask, CC_box…), comme dans le rapport wwPDB.

**Quand l'utiliser**

- Modèle final ou quasi final ; comparaison de versions ; avant la soumission.

**À éviter / pièges**

- Comme seul critère : complétez par le Q-score et l'inspection visuelle.

**Entrées conseillées** : Modèle + carte déposée.

**Conseils**

- Voir le guide (chapitre Validation) pour les valeurs cibles selon la résolution.

**Étapes suivantes** : Pre-deposition checks, ISOLDE session

### EMRinger

`phenix_emringer` — logiciel : `phenix`

Évalue l'ajustement des chaînes latérales via la densité autour des atomes Cγ (EMRinger).

**Quand l'utiliser**

- Cartes meilleures que ≈ 4,5 Å ; détecter un mauvais registre ou une main inversée.

**À éviter / pièges**

- Basse résolution : le score n'est plus informatif.

**Entrées conseillées** : Modèle + carte.

**Conseils**

- Score > 2 : bon à < 4 Å ; ≈ 1 : médiocre ; proche de 0 : registre ou main douteux.

**Étapes suivantes** : ISOLDE session

### Map-model validation (Q-score, FSC)

`mapmodel_validation` — intégré (aucun logiciel externe)

Métriques carte–modèle intégrées : Q-score par atome et par résidu comparé à la valeur attendue, FSC carte–modèle, CC_mask/CC_box, inclusion d'atomes au contour suggéré.

**Quand l'utiliser**

- Toujours, pour chaque modèle candidat (rapide, sans logiciel externe).
- Repérer les résidus mal soutenus par la densité ; comparer ModelAngelo et CryoAtom2.

**À éviter / pièges**

- Carte modifiée par un réseau : validez contre la carte primaire.

**Entrées conseillées** : Modèle + carte déposée (+ FSC demi-cartes pour superposer les courbes).

**Conseils**

- Q-score moyen proche de la valeur attendue à cette résolution : bon accord ; résidus avec Q < 0,3 : à revoir.
- La FSC carte–modèle à 0,5 doit être proche de la résolution FSC demi-cartes à 0,143.

**Étapes suivantes** : ISOLDE session, Sequence register check (checkMySequence), Pre-deposition checks

### MolProbity geometry

`phenix_molprobity` — logiciel : `phenix`

Géométrie seule : clashs, Ramachandran, rotamères, déviations Cβ, CaBLAM (MolProbity).

**Quand l'utiliser**

- Contrôle rapide entre deux sessions ISOLDE/Coot ; liste de problèmes à corriger dans Coot.

**À éviter / pièges**

- Ne dit rien de l'accord avec la carte.

**Entrées conseillées** : Le modèle.

**Conseils**

- Le script molprobity_coot.py produit une liste de tâches à ouvrir dans Coot.

**Étapes suivantes** : Coot session, ISOLDE session

### Sequence register check (checkMySequence)

`checkmysequence` — logiciel : `checkmysequence`

Vérifie l'attribution de séquence du modèle dans la carte (checkMySequence) : décalages de registre, chaînes qui ne correspondent à aucune séquence, différences avec la séquence attendue, ruptures de chaîne sans trou de numérotation.

**Quand l'utiliser**

- Avant tout dépôt : un registre décalé passe inaperçu dans MolProbity et dans les scores carte–modèle.
- Après une construction automatique (ModelAngelo, CryoAtom2) ou une reconstruction manuelle d'une région floue.
- Gros complexe : vérifier que chaque chaîne porte la bonne séquence (sous-unités paralogues).

**À éviter / pièges**

- Cartes à plus de 4–4,5 Å : les chaînes latérales ne sont plus assez visibles, les résultats deviennent peu fiables.

**Entrées conseillées** : Le modèle, la carte dans laquelle il a été construit et toutes les séquences de l'échantillon (FASTA).

**Conseils**

- Un décalage signalé indique de combien de résidus déplacer la séquence : corrigez dans Coot ou ISOLDE puis relancez.
- « Tracing issues » : des fragments voisins proposent des décalages différents, le tracé de la chaîne est douteux.
- Une chaîne « non identifiée » manque souvent du FASTA, ou a été construite hors de la densité.
- Il faut HMMER (hmmsearch) dans l'environnement de checkMySequence.

**Étapes suivantes** : ISOLDE session, Coot session, wwPDB validation report (OneDep)

### wwPDB validation report (OneDep)

`wwpdb_validation` — logiciel : `onedep`

Rapport de validation officiel du wwPDB (PDF et XML), calculé par le service de validation OneDep : le même document que reçoivent les journaux et les relecteurs.

**Quand l'utiliser**

- Modèle final, juste avant le dépôt : on découvre les problèmes avant les annotateurs.
- Pour joindre le rapport à la soumission d'un article.

**À éviter / pièges**

- Données confidentielles que votre équipe ne veut pas envoyer : le modèle et la carte sont transmis au serveur du wwPDB.
- Modèle encore en cours d'affinement : utilisez d'abord les validations locales (plus rapides).

**Entrées conseillées** : Le modèle final et la carte primaire (celle qui sera déposée).

**Conseils**

- Nécessite le client du wwPDB (pip install onedep_api) et un accès internet depuis le serveur CryoPlug.
- Les centiles comparent votre modèle à toutes les entrées de la PDB et à celles de résolution voisine : plus haut, c'est mieux.
- Une grosse entrée EM peut demander une heure ou plus de calcul côté wwPDB : réglez « Maximum wait » au besoin.

**Étapes suivantes** : Deposition package

## Dépôt

### Deposition package

`deposition_package` — intégré (aucun logiciel externe)

Assemble tout le nécessaire au dépôt wwPDB/EMDB : mmCIF, cartes, FSC XML, rapports, niveau de contour recommandé, brouillon des méthodes avec citations et brouillon de « Table 1 ».

**Quand l'utiliser**

- Le modèle est final et validé ; préparation de l'article.

**À éviter / pièges**

- Modèle encore en cours d'affinement : refaites le paquet à la fin.

**Entrées conseillées** : Modèle final, carte primaire, demi-cartes, masque, FSC, checklist.

**Conseils**

- Relisez methods_draft.md et table1_draft.md : les cases de collecte de données restent à remplir.
- Lancez aussi le job « wwPDB validation report » : le rapport officiel, celui que verront les relecteurs.

### Pre-deposition checks

`predeposition_check` — intégré (aucun logiciel externe)

Checklist automatique avant OneDep : cohérence cartes/demi-cartes/masque, modèle dans la boîte, ajustement, chaînes, résidus inconnus, occupations, B-factors, clashs sévères, ruptures de chaîne, accord avec la séquence.

**Quand l'utiliser**

- Avant chaque soumission ; après tout changement de carte, de masque ou de modèle.

**Entrées conseillées** : Modèle final + carte primaire + demi-cartes + masque + séquence.

**Conseils**

- Corrigez les FAIL ; chaque WARN doit être compris et, si besoin, justifié auprès des annotateurs.

**Étapes suivantes** : Deposition package, wwPDB validation report (OneDep)

## Utilitaires

### Custom command

`custom_command` — logiciel : `shell`

Lance n'importe quel programme sur les données du projet (script bash avec des emplacements {map}, {half_map_a}, {model}…), et enregistre les fichiers produits comme sorties.

**Quand l'utiliser**

- Logiciel pas encore intégré (DeepMainmast, EModelX, CryoREAD, scripts maison…).

**À éviter / pièges**

- Opérations déjà couvertes par un job dédié (meilleure traçabilité et rapports).
- Avec des comptes utilisateurs, réservé aux administrateurs : le script s'exécute sous le compte du serveur.

**Entrées conseillées** : Les entrées utiles au programme ; choisissez l'environnement (configuration d'un outil).

**Conseils**

- Indiquez les motifs de sortie (ex. result*.mrc) pour récupérer cartes et modèles.

### Merge models

`merge_models` — intégré (aucun logiciel externe)

Combine les chaînes de plusieurs modèles (domaines ou sous-unités dockés séparément, ligand) en un seul.

**Quand l'utiliser**

- Après docking domaine par domaine ; ajout d'un ligand (coordonnées eLBOW) ou d'une sous-unité.

**À éviter / pièges**

- Modèles qui se chevauchent dans l'espace : vérifiez dans le visualiseur.

**Entrées conseillées** : Deux à quatre modèles.

**Conseils**

- Les identifiants de chaîne en conflit sont renommés automatiquement (voir le log).

**Étapes suivantes** : Real-space refinement (Phenix), ISOLDE session

### Model operations

`model_tools` — intégré (aucun logiciel externe)

Édition de modèle : conversion PDB↔mmCIF, sélection/renommage de chaînes, suppression d'hydrogènes, eaux, ligands ou conformations alternatives, remise à zéro des B-factors/occupations.

**Quand l'utiliser**

- Préparer l'entrée d'un logiciel exigeant (format, hydrogènes).
- Renommer les chaînes comme dans l'article, retirer des copies ou un partenaire.

**À éviter / pièges**

- Remettre des B-factors à une valeur unique sur le modèle final déposé.

**Entrées conseillées** : Le modèle.

**Conseils**

- Le mmCIF est recommandé (identifiants de chaîne longs, grands complexes).

**Étapes suivantes** : Real-space refinement (Phenix)

### Render images (ChimeraX)

`chimerax_render` — logiciel : `chimerax`

Images ChimeraX hors écran du modèle et/ou de la carte sous trois vues orthogonales.

**Quand l'utiliser**

- Figures rapides, rapports de labo, suivi d'un projet.

**À éviter / pièges**

- Figures finales de publication : faites-les dans ChimeraX interactif.

**Entrées conseillées** : Carte et/ou modèle.

**Conseils**

- Nécessite une version de ChimeraX qui supporte --offscreen.
