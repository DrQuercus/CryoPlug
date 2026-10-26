# Référence des jobs CryoPlug

Fichier généré par `cryoplug docs-jobs` à partir de `cryoplug/jobhelp.py` — ne pas éditer à la main.
Les mêmes fiches sont affichées dans l'interface (constructeur de job, détails, page d'aide).

- [Import](#import)
- [Prétraitement (films, CTF)](#prétraitement-films-ctf)
- [Sélection et extraction de particules](#sélection-et-extraction-de-particules)
- [Classification 2D / 3D](#classification-2d--3d)
- [Reconstruction (RELION)](#reconstruction-relion)
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

**Étapes suivantes** : Process predicted model (Phenix), Fit in map (rigid body), Rigid-body fit (ChimeraX), Dock in map (Phenix), Map-model validation (Q-score, FSC)

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

**Étapes suivantes** : Directional resolution (3D FSC), LocScale 2, ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), Import sequence, Mask drawn in the 3D viewer, RELION local refinement

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

### Import movies (RELION)

`relion_import` — intégré (aucun logiciel externe)

Importe les films bruts (ou des micrographies déjà alignées) avec leur groupe optique : taille de pixel, tension, aberration sphérique, contraste d'amplitude, gain et MTF. Écrit le movies.star de relion_import ; les dossiers des films sont liés, rien n'est copié. Le point de départ du pipeline RELION.

**Quand l'utiliser**

- Démarrer un nouveau jeu de données à partir des films du microscope (TIFF, EER, MRC, MRC compressés).
- Reprendre dans RELION des micrographies déjà sommées par un autre logiciel.

**À éviter / pièges**

- Particules déjà extraites ailleurs (CryoSPARC) : « Import particles » les prend directement.
- Sessions de tailles de pixel ou de détecteurs différents dans un seul import : importez-les séparément (un groupe optique chacune) puis réunissez-les avec « Join star files ».

**Entrées conseillées** : Un motif (/data/session/Movies/*.tiff, « ** » pour les sous-dossiers) ou le dossier des films ; la référence de gain quand les films ne sont pas corrigés.

**Conseils**

- Taille de pixel des films tels qu'enregistrés : en super-résolution, la taille super-résolution, et un binning de 2 dans la correction de mouvement.
- La dose par image (e⁻/Å²) sert à la pondération par la dose ; pour l'EER, la dose par fraction.
- Rotation et retournement du gain comme dans MotionCor2 (-RotGain / -FlipGain) : vérifiez l'orientation sur une première micrographie corrigée.
- Le fichier MTF du détecteur (wiki RELION) sert au post-traitement.

**Étapes suivantes** : Motion correction (RELION), Estimate gain reference (RELION)

### Import particles

`import_particles` — intégré (aucun logiciel externe)

Importe des particules pour l'analyse d'hétérogénéité (cryoDRGN) : un job CryoSPARC ou un fichier .cs (fusionné avec son fichier « passthrough »), ou un fichier .star de RELION. Seules les métadonnées sont lues : les images restent où elles sont.

**Quand l'utiliser**

- Les particules ne viennent pas d'un job déjà importé avec « Import from CryoSPARC ».
- Particules exportées d'un autre projet, ou d'un autre logiciel (.star).

**À éviter / pièges**

- Particules sans poses (extraction, classification 2D) pour un entraînement cryoDRGN classique : il lui faut un raffinement consensus. cryoDRGN-AI (ab initio), lui, les accepte.

**Entrées conseillées** : Le dossier du job de raffinement consensus (ses particules portent les poses), un .cs ou un .star ; pour cryoDRGN-AI, n'importe quel job qui contient les particules (Select 2D, extraction…).

**Conseils**

- « Import from CryoSPARC » importe déjà les particules du job en même temps que les cartes.
- Si les chemins des images sont cassés, indiquez le dossier des images (Image folder).

**Étapes suivantes** : cryoDRGN input check (backprojection), cryoDRGN training, cryoDRGN-AI ab initio reconstruction, RELION local refinement, RELION reconstruction, 2D classification (RELION)

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

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), Complex prediction (Boltz-2), AlphaFold2 prediction (ColabFold)

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

## Prétraitement (films, CTF)

### CTF estimation (CTFFIND)

`relion_ctffind` — logiciel : `relion`

Ajuste la fonction de transfert de contraste de chaque micrographie avec CTFFIND 4.1 ou 5 (lancé par relion_run_ctffind) : défocus, astigmatisme, déphasage (plaque de phase) et résolution jusqu'à laquelle l'ajustement tient.

**Quand l'utiliser**

- Après la correction de mouvement, avant le picking : les particules extraites emportent leur CTF.

**À éviter / pièges**

- Gctf n'est pas proposé : il ne lit pas le float16 et n'est plus maintenu.

**Entrées conseillées** : Les micrographies de « Motion correction (RELION) ».

**Conseils**

- Résolution maximale 5 Å par défaut, 3-4 Å pour de bonnes données.
- Les spectres de puissance de la correction de mouvement donnent de meilleurs anneaux de Thon (indispensables pour des micrographies en float16).
- Le meilleur critère de tri est la résolution de l'ajustement : filtrez avec « Curate micrographs » (≤ 6 Å par exemple).
- L'image des ajustements (meilleur, médian, pire) permet de les vérifier d'un coup d'œil.
- Avancé : « Sample tilt search » (CTFFIND 4.1.15 ou plus récent) ajuste l'inclinaison de l'échantillon (collecte inclinée) ; « Extra ice statistics » ajoute la fraction de puissance et le kurtosis de l'anneau de glace (tri des micrographies de glace cristalline), avec les spectres de la correction de mouvement.

**Étapes suivantes** : Curate micrographs, Auto-picking (RELION)

### Curate micrographs

`relion_curate` — intégré (aucun logiciel externe)

Trie les micrographies sur leur ajustement CTF, leur défocus, leur astigmatisme, leur mouvement et la glace (comme « Manually Curate Exposures » de CryoSPARC). En mode interactif, le job attend : distributions dont on tire les limites sur les histogrammes, nuage de points de deux mesures, tableau triable des micrographies, et chaque micrographie avec son ajustement CTF et sa dérive, acceptée ou rejetée à la main. Ou bien il applique d'un coup les seuils donnés. Les micrographies rejetées forment une seconde sortie.

**Quand l'utiliser**

- Avant le picking : retirer glace épaisse, dérive, contamination, ajustements CTF ratés.
- Ne garder qu'une gamme de défocus pour un essai rapide.
- Depuis l'estimation de la CTF : « Curate… » dans le navigateur d'expositions crée ce job avec les limites essayées.

**À éviter / pièges**

- Des seuils fixés à l'aveugle : regardez d'abord les histogrammes et quelques micrographies (mode interactif).

**Entrées conseillées** : Les micrographies avec CTF ; le mouvement et la dérive sont relus dans la correction de mouvement quand l'estimation de la CTF les a perdus. En option, des particules ou des picks : leur nombre par micrographie devient un critère et ceux des micrographies gardées forment une sortie.

**Conseils**

- Résolution d'ajustement CTF ≤ 5-8 Å selon les données ; mouvement total < 30-50 Å.
- Clavier : ← → pour passer d'une micrographie à l'autre (dans l'ordre et le filtre du tableau), A accepte, R rejette, U rend la décision aux limites ; double-clic sur un histogramme : plus de limite.
- Le travail est enregistré au fil de l'eau ; Done sans rien envoyer (agent) applique les seuils des paramètres.
- La sortie « rejected » permet de vérifier ce qui a été écarté.
- Avec les picks ou les particules connectés : écartez les micrographies trop chargées (agrégats, carbone) ou vides ; les particules des micrographies gardées suivent (sortie « particles » ou « coordinates »).

**Étapes suivantes** : Auto-picking (RELION), Inspect picks / manual picking, Topaz training (RELION)

### Denoise micrographs (Topaz)

`relion_topaz_denoise` — logiciel : `relion`

Débruite les micrographies avec le réseau pré-entraîné de Topaz (topaz denoise), comme le « Micrograph Denoiser » de CryoSPARC : les particules ressortent pour le picking à l'œil ou automatique et pour l'inspection. Les picks faits sur les micrographies débruitées s'extraient des originales : l'extraction traduit les noms.

**Quand l'utiliser**

- Particules peu contrastées, petites ou en glace épaisse : picking manuel ou LoG sur micrographies débruitées.
- Préparer un jeu d'entraînement Topaz par picking manuel.

**À éviter / pièges**

- Estimer la CTF ou extraire sur les micrographies débruitées : le bruit y est modifié ; utilisez les originales.

**Entrées conseillées** : Les micrographies corrigées du mouvement (avec CTF).

**Conseils**

- « Micrographs » : quelques dizaines suffisent pour juger ou entraîner un picking.
- GPU conseillé (sur CPU, plusieurs secondes par micrographie).
- Connectez à l'extraction les micrographies originales et les picks faits sur les débruitées.

**Étapes suivantes** : Inspect picks / manual picking, Auto-picking (RELION)

### Estimate gain reference (RELION)

`relion_estimate_gain` — logiciel : `relion`

Estime une référence de gain à partir des films eux-mêmes (relion_estimate_gain) : l'inverse de la moyenne de milliers d'images (la moyenne des comptes pour l'EER). Les films ressortent avec ce gain attaché, que la correction de mouvement applique.

**Quand l'utiliser**

- Référence de gain perdue, mal orientée ou d'une autre session ; motifs fixes visibles sur les micrographies.
- Films en mode comptage non corrigés du gain.

**À éviter / pièges**

- Trop peu d'images : il en faut des milliers pour un gain sans bruit.
- Des films déjà corrigés du gain (le gain estimé serait plat et inutile).

**Entrées conseillées** : Les films importés (non corrigés du gain).

**Conseils**

- 5000 images tirées au hasard pour commencer ; -1 prend tous les films (long).
- EER : l'option --dont_invert est ajoutée d'office (le gain des EER est la moyenne des comptes).
- Vérifiez l'image du gain dans le rapport : pixels morts ou chauds, bandes du détecteur.

**Étapes suivantes** : Motion correction (RELION)

### Live preprocessing (RELION)

`relion_live` — logiciel : `relion`

Traite les films pendant la collecte, comme CryoSPARC Live : à chaque cycle, les nouveaux films du dossier sont importés, corrigés du mouvement, leur CTF estimée, triés sur des seuils et, au choix, pickés et extraits (les jobs RELION de CryoPlug avec --only_do_unfinished). Le panneau suit la session : comptes, cadence, CTF, défocus, mouvement, glace, expositions et picks ; « Stop after this cycle » termine le job avec ses sorties.

**Quand l'utiliser**

- Pendant une session au microscope : juger la grille et la glace tout de suite, ajuster la collecte.
- Arriver à la fin de la collecte avec des micrographies triées et des particules prêtes pour la 2D.

**À éviter / pièges**

- Un dossier où les films arrivent incomplets sans délai : réglez « A movie is complete after » (30 s par défaut) d'après la vitesse d'écriture.
- Des seuils de tri trop stricts au début : les micrographies rejetées restent dans « All micrographs » et se trient à nouveau ensuite.

**Entrées conseillées** : Aucune : le motif des films (ou leur dossier), la taille de pixel, la dose par image et la référence de gain.

**Conseils**

- Picking LoG avec les diamètres minimum et maximum de la particule ; extraction à boîte réduite (64-128 px) pour une 2D rapide.
- Kill arrête tout sans sorties ; « Stop after this cycle » traite les derniers films puis donne les sorties.
- « Stop after no new movie for » arrête le job seul à la fin de la session.
- Limites d'acceptation en direct : tirez-les sur les histogrammes des expositions puis « Apply to the session » ; tout est retrié au cycle suivant. « Pause » fait attendre les films, « Resume » reprend.
- « Streaming 2D classification » : classes 2D des particules déjà extraites, relancées quand assez de nouvelles particules arrivent ; à l'arrêt, la galerie permet « Select these classes ».
- Depuis le job terminé, « Curate… » dans les expositions crée un tri interactif de toutes les micrographies.

**Étapes suivantes** : Curate micrographs, 2D classification (RELION), Inspect picks / manual picking, Particle extraction (RELION)

### Motion correction (RELION)

`relion_motioncorr` — logiciel : `relion`

Aligne les images de chaque film (mouvement induit par le faisceau, globalement puis par patchs) et les somme avec pondération par la dose : l'implémentation CPU de RELION ou MotionCor2 sur GPU. Écrit aussi les spectres de puissance sommés que lit CTFFIND et la trajectoire de chaque film (pour le polissage).

**Quand l'utiliser**

- Toujours, juste après l'import des films.
- Films super-résolution : binning 2 ici.

**À éviter / pièges**

- Micrographies importées déjà sommées : passez directement à l'estimation de la CTF.
- MotionCor2 sans GPU : prenez l'implémentation de RELION (CPU).

**Entrées conseillées** : Les films de « Import movies (RELION) ».

**Conseils**

- Patchs 5 × 5 pour K2, K3 ou Falcon 4 (1 × 1 = mouvement global seulement, le défaut du GUI de RELION).
- Implémentation RELION : un nombre de threads qui divise le nombre d'images ; chaque processus MPI traite un film.
- float16 divise la place disque par deux ; gardez les spectres de puissance pour CTFFIND.
- Le graphe du mouvement cumulé montre les films aberrants : retirez-les avec « Curate micrographs ».

**Étapes suivantes** : CTF estimation (CTFFIND)

## Sélection et extraction de particules

### Auto-picking (RELION)

`relion_autopick` — logiciel : `relion`

Repère les particules sur les micrographies (relion_autopick) : détection de blobs par Laplacien de Gaussienne (sans modèle), correspondance avec des modèles (classes 2D ou projections d'une carte 3D) ou Topaz (réseau de neurones, général ou entraîné sur ces données).

**Quand l'utiliser**

- Nouveau jeu de données : LoG pour une première série, puis les classes 2D comme modèles ou Topaz entraîné.
- Repicker tout le jeu avec un modèle Topaz entraîné sur les bonnes particules.

**À éviter / pièges**

- Seuil LoG trop bas : beaucoup de faux positifs (glace, carbone) qui partent en 2D mais coûtent du temps.
- Modèles 3D d'un autre jeu de données (autre taille de pixel, autre contraste) sans vérification.

**Entrées conseillées** : Les micrographies avec CTF ; selon la méthode, des classes 2D sélectionnées, une carte 3D ou un modèle Topaz.

**Conseils**

- LoG : diamètres minimum et maximum encadrant la particule ; ajustez le seuil (σ) d'après l'image des particules repérées.
- Modèles 2D : recentrez les classes (option de « Select 2D classes ») ; distance minimale ≈ le diamètre.
- Topaz : le diamètre est obligatoire ; le seuil de figure de mérite se règle ensuite à l'extraction (-1 ou 0).
- L'histogramme des figures de mérite aide à choisir ce seuil ; « Inspect and edit… » le règle sur les micrographies.

**Étapes suivantes** : Particle extraction (RELION), Inspect picks / manual picking

### Inspect picks / manual picking

`relion_pick_inspect` — intégré (aucun logiciel externe)

Inspecte les particules repérées sur chaque micrographie et les corrige à la main (comme « Inspect Picks » et « Manual Picker » de CryoSPARC) : seuil de figure de mérite et gamme de « power score » (puissance locale autour de chaque pick) réglés sur leurs histogrammes, avec le nuage des deux, particules retirées ou ajoutées d'un clic, zoom, déplacement, filtre passe-bas de l'affichage. Sans coordonnées en entrée, c'est un picking entièrement manuel ; en mode « Filaments », on clique le début puis la fin de chaque filament, comme le picking manuel d'hélices de RELION.

**Quand l'utiliser**

- Après le picking automatique : choisir le seuil de figure de mérite en regardant les micrographies.
- Écarter les picks sur la glace cristalline, le carbone et la contamination (power score élevé) ou sur la glace vide (power score faible).
- Picker à la main quelques centaines de particules pour entraîner Topaz ou faire de premières classes 2D.
- Filaments, fibres amyloïdes, hélices : tracer les tubes, puis extraction avec « Helical processing » qui les découpe en segments.
- Vérifier qu'un picking n'a pas pris la glace, le carbone ou les bords des trous.

**À éviter / pièges**

- Corriger des milliers de micrographies une à une : réglez plutôt le seuil puis nettoyez en 2D.
- Un seuil choisi sur une seule micrographie : passez-en plusieurs (flèches ← →).

**Entrées conseillées** : Les micrographies (avec CTF) et, si on inspecte un picking, ses coordonnées ; le diamètre est obligatoire pour un picking manuel.

**Conseils**

- Glissez sur l'histogramme pour le seuil ; « Hidden » montre en pointillés les particules sous le seuil et en rouge celles retirées.
- Le travail est enregistré au fil de l'eau : on peut fermer la page et revenir avant Done.
- Les particules ajoutées à la main reçoivent une figure de mérite au-dessus de toutes les autres : un seuil appliqué plus tard les garde.
- Depuis un picking terminé, « Inspect and edit… » crée directement ce job avec le seuil affiché.
- Power score : les deux poignées de son histogramme (échelle log quand il s'étale) gardent une gamme ; le nuage figure de mérite / power score montre la boîte gardée. Le calcul lit chaque micrographie une fois (désactivable pour de très gros jeux).
- Filaments : le diamètre est celui du tube ; un clic sur un filament le retire, Échap annule un début. L'extraction refuse ces coordonnées sans « Helical processing » (montée, diamètre du tube) qui les découpe.

**Étapes suivantes** : Particle extraction (RELION), Topaz training (RELION)

### Particle extraction (RELION)

`relion_extract` — logiciel : `relion`

Découpe les particules dans les micrographies (relion_preprocess --extract) avec leur CTF : boîte, réduction d'échelle, inversion de contraste et normalisation comme RELION les attend. Ré-extrait aussi des particules raffinées (recentrées, autre boîte ou autre taille de pixel).

**Quand l'utiliser**

- Après le picking.
- Après un raffinement à taille de pixel réduite : ré-extraction à pleine échelle, recentrée sur les positions raffinées.

**À éviter / pièges**

- Boîte trop serrée : comptez 1,5 à 2 fois le diamètre (la CTF délocalise le signal hors de la particule).

**Entrées conseillées** : Les micrographies avec CTF et les coordonnées du picking (ou des particules à ré-extraire).

**Conseils**

- Réduire l'échelle (256 → 64 px par exemple) accélère la 2D et le modèle initial ; ré-extrayez à pleine échelle avant le raffinement final.
- Boîtes paires uniquement ; la boîte réduite aussi.
- Topaz : un seuil de figure de mérite (-1 par exemple) écarte les mauvais picks.
- Filaments : « Helical processing » découpe les filaments en segments (diamètre du tube, montée, unités asymétriques par segment).

**Étapes suivantes** : 2D classification (RELION), 3D initial model (RELION), 3D auto-refine (RELION)

### Topaz training (RELION)

`relion_topaz_train` — logiciel : `relion`

Entraîne un réseau Topaz sur ce jeu de données (relion_autopick --topaz_train) à partir de particules propres (celles des bonnes classes 2D) ou de coordonnées vérifiées ; le modèle sert ensuite à repicker tout le jeu.

**Quand l'utiliser**

- Particules difficiles pour le LoG : petites, irrégulières, peu contrastées.
- Après une première classification 2D : les bonnes particules montrent au réseau à quoi ressemble la protéine.

**À éviter / pièges**

- Particules d'entraînement non nettoyées : le réseau apprend aussi la glace et le carbone.
- Sans GPU : l'entraînement est très lent.

**Entrées conseillées** : Des micrographies (quelques centaines suffisent) et les particules sélectionnées (ou des coordonnées).

**Conseils**

- Particules par micrographie : une estimation large de la moyenne réelle.
- Le modèle gardé est celui de l'époque à la meilleure précision de test (AUPRC), tracée dans le rapport.

**Étapes suivantes** : Auto-picking (RELION)

## Classification 2D / 3D

### 2D classification (RELION)

`relion_class2d` — logiciel : `relion`

Trie les particules en moyennes de classes 2D (relion_refine) : VDAM, l'algorithme par gradient rapide de RELION 4/5, ou l'EM classique. Les moyennes s'affichent pendant le calcul ; les particules gardent leur classe pour la sélection des bonnes classes.

**Quand l'utiliser**

- Nettoyer les particules après le picking : glace, carbone, agrégats, particules cassées.
- Voir les vues de la particule et choisir des modèles de picking.

**À éviter / pièges**

- VDAM avec plusieurs processus MPI : non supporté (des threads à la place) ; l'EM accepte le MPI.
- Trop peu de classes pour un grand jeu : 100-200 classes isolent mieux les vues rares.

**Entrées conseillées** : Les particules extraites (RELION ou CryoSPARC).

**Conseils**

- VDAM, 200 mini-lots : bons résultats pour la plupart des données ; 50 à 200 classes.
- Masque circulaire un peu plus grand que la particule (0 = 90 % de la boîte).
- T = 2 en 2D ; limiter la résolution de l'étape E (7-12 Å) évite le sur-ajustement des petites particules.
- Dans la galerie, cliquez des classes puis « Select these classes » : la sélection est créée et lancée.
- Filaments : « Helical processing » (diamètre du tube, recherche bimodale de ψ, décalages limités le long de l'axe).
- Les classes suffisent avant la fin ? « Stop », puis « Mark as complete » : les moyennes et les particules de la dernière itération écrite deviennent les sorties (VDAM écrit toutes les 10 itérations). « Resume » relance un run arrêté de sa dernière itération.

**Étapes suivantes** : Select 2D classes, 2D classification (RELION), Curate particles (statistics)

### 3D classification (RELION)

`relion_class3d_global` — logiciel : `relion`

Trie les particules en classes 3D contre une référence (relion_refine --K) : recherches angulaires globales, locales ou aucune (classification à poses fixes). Chaque classe donne une carte et ses particules ; Blush aide les particules petites ou flexibles.

**Quand l'utiliser**

- Nettoyer encore les particules après la 2D (particules cassées, mauvaises conformations).
- Séparer des états discrets : ligand lié ou libre, conformations ouverte et fermée.

**À éviter / pièges**

- Hétérogénéité continue : multi-corps, DynaMight, cryoDRGN ou la 3D variability de CryoSPARC la décrivent mieux.
- Référence trop détaillée : filtre passe-bas initial de 40 à 60 Å pour un modèle initial.

**Entrées conseillées** : Les particules (sélection 2D) et une référence (modèle initial ou carte raffinée) ; un masque en option.

**Conseils**

- 4 à 8 classes, 25 itérations, T = 4.
- Sans alignement, sur des particules raffinées et avec un masque : classification rapide et focalisée.
- Les cartes des classes se jouent comme une série dans le visualiseur 3D.
- Le rapport suit la part de chaque classe au fil des itérations (convergence) et montre les directions de vue de chaque classe.
- Filaments : « Helical processing » avec twist et montée initiaux (et leur recherche locale).
- Un run en échec ou tué (mémoire, nœud perdu, temps de file dépassé) se relance de sa dernière itération : « Resume » (le « Continue » de RELION, fichiers gardés, sorties sous run_ct<itération>) ; « Mark as complete » le termine avec les sorties de cette itération (comme dans CryoSPARC).

**Étapes suivantes** : 3D auto-refine (RELION), 3D classification (RELION), Heterogeneous refinement (RELION), Curate particles (statistics)

### Amyloid initial model (RELION)

`relion_helix_inimodel2d` — logiciel : `relion`

Construit le modèle initial 3D d'une fibre amyloïde à partir de ses classes 2D (relion_helix_inimodel2d, tutoriel amyloïdes de RELION) : les moyennes de classes sont alignées en une longue image 2D couvrant tout un cross-over, puis tournées autour de l'axe selon la distance entre cross-overs. Une carte par classe, référence du raffinement hélical 3D.

**Quand l'utiliser**

- Fibres amyloïdes sans modèle initial : après une classification 2D de segments en grandes boîtes.
- Plusieurs polymorphes : une carte par classe (« Classes »).

**À éviter / pièges**

- Des classes 2D floues ou de petite boîte : il faut voir la torsion de la fibre sur les classes.
- Une distance de cross-over approximative : mesurez-la sur les micrographies ou de longues classes.

**Entrées conseillées** : Les bonnes classes 2D (Select 2D classes) ou les classes d'une classification 2D (les vides sont écartées).

**Conseils**

- Symétrie 2 dans la section pour deux protofilaments reliés par un axe d'ordre 2 (pseudo-2₁).
- Puis raffinement hélical 3D avec ce modèle (twist ≈ −180 × montée / cross-over, montée 4,75 Å).
- Recherche de décalage ~10 Å et d'angle ~15° ; quelques dizaines d'itérations suffisent.

**Étapes suivantes** : 3D auto-refine (RELION), 3D classification (RELION), Helical symmetry (RELION)

### Heterogeneous refinement (RELION)

`relion_hetero_refine` — logiciel : `relion`

Répartit les particules entre plusieurs cartes de référence tout en les raffinant toutes (comme le « Heterogeneous Refinement » de CryoSPARC ; relion_refine avec une référence par classe). Les cartes venues d'autres jobs sont rééchantillonnées sur la grille des particules.

**Quand l'utiliser**

- Nettoyer un jeu de particules : une bonne carte et des leurres (carte de déchets, particule cassée, micelle vide) qui attirent les mauvaises particules.
- Après un modèle initial à plusieurs classes : séparer conformations ou compositions avec ces classes comme références.

**À éviter / pièges**

- Des références identiques : elles se partagent les particules au hasard ; partez de cartes réellement différentes.
- Des références trop détaillées : le filtre passe-bas initial (40 Å par défaut) évite le biais de modèle.

**Entrées conseillées** : Les particules, au moins deux cartes (une par classe), un masque en option.

**Conseils**

- Leurres : une carte de bruit filtrée, une classe « poubelle » d'une classification, une carte d'un mauvais modèle initial.
- Le rapport donne la part de chaque classe au fil des itérations, leur résolution estimée et les directions de vue par classe ; les particules de chaque classe sont des sorties.

**Étapes suivantes** : 3D auto-refine (RELION), 3D classification (RELION)

### Select 2D classes

`relion_select2d` — logiciel : `relion`

Garde les particules des bonnes classes 2D. Comme dans CryoSPARC, le job affiche les moyennes et attend : cliquez les bonnes classes (triées par taille ou résolution), puis Done. Sélection aussi par liste ou automatique (class ranker de RELION). Donne les particules gardées, les rejetées et les moyennes sélectionnées (modèles de picking).

**Quand l'utiliser**

- Après chaque classification 2D.
- Préparer des modèles 2D pour le picking par correspondance de modèles (option de recentrage).
- Sélection automatique dans une chaîne de traitement (score minimum du class ranker).

**À éviter / pièges**

- Ne garder que les plus belles classes : les vues rares sont souvent dans des classes moins nettes ; préférez plusieurs tours de 2D.

**Entrées conseillées** : Les moyennes de classes d'une « 2D classification (RELION) ».

**Conseils**

- Maj + clic sélectionne une série de classes ; boutons Tout, Aucune, Inverser.
- « Au plus N particules par classe » équilibre les vues avant un modèle initial.
- Class ranker : score de 0 à 1 (0,5 par défaut) ; il demande l'environnement Python de RELION 5.
- « By thresholds… » coche d'un coup les classes d'au moins N particules, à résolution donnée ou meilleure (et score du class ranker).

**Étapes suivantes** : 3D initial model (RELION), 2D classification (RELION), 3D classification (RELION), 3D auto-refine (RELION), Topaz training (RELION), Auto-picking (RELION)

### Select 3D classes

`relion_select3d` — intégré (aucun logiciel externe)

Garde les particules des classes 3D cliquées dans une galerie des cartes de classes (projections selon z, y, x et coupe centrale), ou listées : réunit les bonnes classes d'une classification 3D ou d'un raffinement hétérogène ; les autres forment une seconde sortie.

**Quand l'utiliser**

- Après une classification 3D : réunir les classes de la bonne conformation avant le raffinement.
- Après un raffinement hétérogène avec leurres : garder la classe de la protéine.

**À éviter / pièges**

- Des classes de conformations différentes réunies : la carte moyenne les mélange.

**Entrées conseillées** : Les « Classified particles » d'une classification 3D ou d'un raffinement hétérogène (elles portent le modèle des classes).

**Conseils**

- Depuis la galerie d'une classification terminée, « Select these classes » crée et lance ce job.
- Ouvrez les cartes des classes (sorties map_N) dans le visualiseur 3D pour juger les détails.

**Étapes suivantes** : 3D auto-refine (RELION), 3D classification (RELION)

## Reconstruction (RELION)

### 3D auto-refine (RELION)

`relion_refine3d` — logiciel : `relion`

Raffinement 3D gold-standard (relion_refine --auto_refine) : recherches globales puis locales à mesure qu'il converge, deux demi-jeux indépendants. Donne la carte, les demi-cartes, la FSC, les particules raffinées et, avec un masque, la carte post-traitée.

**Quand l'utiliser**

- Après la classification : la carte haute résolution.
- Après une ré-extraction à pleine échelle, la CTF refinement ou le polissage : un nouveau raffinement.

**À éviter / pièges**

- Moins de 3 processus MPI : impossible (un maître et un processus par demi-jeu).

**Entrées conseillées** : Les particules et une référence (classe 3D, modèle initial ou carte raffinée) ; un masque doux (« Mask creation (RELION) ») pour la FSC corrigée et le post-traitement.

**Conseils**

- Filtre initial 40-60 Å pour un modèle initial, 10-15 Å pour une carte déjà raffinée à partir des mêmes particules.
- Recherches locales à partir de 1,8° (le défaut du GUI de RELION).
- « Solvent-flattened FSC » (avec un masque) aide une petite particule dans une grande boîte.
- Blush (RELION 5) pour les particules petites ou flexibles.
- Filaments : « Helical processing » impose la symétrie hélicoïdale (twist, montée) et peut l'affiner (recherche locale).
- Un run en échec ou tué (mémoire, nœud perdu, temps de file dépassé) se relance de sa dernière itération : « Resume » (le « Continue » de RELION, fichiers gardés, sorties sous run_ct<itération>) ; « Mark as complete » le termine avec les sorties de cette itération (comme dans CryoSPARC).

**Étapes suivantes** : Mask creation (RELION), CTF refinement (RELION), Bayesian polishing (RELION), RELION post-processing, Local resolution (RELION), 3D multi-body refinement (RELION), DynaMight flexibility (RELION), RELION local refinement

### 3D initial model (RELION)

`relion_initial_model` — logiciel : `relion`

Calcule une première carte 3D sans référence (relion_refine --grad --denovo_3dref, VDAM) puis l'aligne sur les axes de symétrie et applique la symétrie (relion_align_symmetry) : l'ab initio de RELION.

**Quand l'utiliser**

- Aucune carte de départ : après une sélection 2D propre.
- Plusieurs classes : séparer dès le départ des structures différentes.

**À éviter / pièges**

- Vues préférentielles extrêmes : le modèle sera déformé (regardez la distribution angulaire ensuite).

**Entrées conseillées** : Quelques milliers à quelques dizaines de milliers de bonnes particules de vues variées (à taille de pixel réduite pour aller vite).

**Conseils**

- Calcul en C1 puis symétrie appliquée : plus robuste que la symétrie imposée dès le départ.
- T = 4 et 200 mini-lots, comme le GUI de RELION.
- La carte est sur l'échelle de gris absolue de RELION : les jobs 3D suivants s'en servent sans --firstiter_cc.
- Arrêté ou tombé en route : « Resume » reprend de la dernière itération, « Mark as complete » aligne et symétrise le modèle de cette itération.

**Étapes suivantes** : 3D classification (RELION), Heterogeneous refinement (RELION), 3D auto-refine (RELION)

### RELION 3D classification (focused)

`relion_class3d` — logiciel : `relion`

Classification 3D RELION de particules avec poses, focalisée par un masque, sans alignement (ou avec recherches locales) : sépare les états d'une région, typiquement sur des particules soustraites.

**Quand l'utiliser**

- Hétérogénéité discrète d'un domaine (ligand lié / libre, conformation ouverte / fermée).
- Après expansion de symétrie + soustraction : trouver les copies dans un état donné.
- Nettoyer un jeu de particules avant un raffinement local.

**À éviter / pièges**

- Hétérogénéité continue : cryoDRGN ou la 3D variability de CryoSPARC la décrivent mieux.
- Particules sans poses : il faut un raffinement consensus d'abord.

**Entrées conseillées** : Particules (soustraites de préférence), la carte de référence (consensus ou raffinement focalisé) et le masque de la région.

**Conseils**

- T = 4 par défaut ; 10 à 40 pour de petites régions soustraites (plus de détails, plus de bruit).
- Sans alignement (--skip_align), 25 itérations suffisent généralement ; regardez la stabilité des classes.
- Chaque classe donne une carte (sur la grille consensus) et ses particules : enchaînez par un raffinement local.

**Étapes suivantes** : RELION local refinement, RELION reconstruction, cryoDRGN training

### RELION local refinement

`relion_local_refine` — logiciel : `relion`

Raffinement 3D gold-standard dans RELION avec recherches angulaires locales autour des poses des particules (raffinement consensus CryoSPARC ou job RELION précédent), focalisé par un masque : l'équivalent RELION du « Local Refinement » de CryoSPARC, avec la régularisation Blush.

**Quand l'utiliser**

- Une région (domaine, sous-complexe, ligand) est floue dans la carte consensus : raffinement local avec son masque.
- Après une soustraction de signal (« RELION signal subtraction ») : raffinement focalisé des particules soustraites, recentrées et recadrées.
- Comparer un raffinement local RELION (Blush) à celui de CryoSPARC sur les mêmes particules.
- Obtenir le modèle RELION (optimiser) nécessaire à la soustraction : une courte passe sur tout le complexe.

**À éviter / pièges**

- Région trop petite (< 100-150 kDa sans signal soustrait) : l'alignement local devient instable ; soustrayez d'abord le reste du complexe et essayez Blush.
- Particules sans poses (sortie de classification 2D) : il faut un raffinement consensus.
- Masque serré au bord de la densité : prenez un masque doux (dilatation + bord ≥ 6 Å), sinon la FSC est surestimée.

**Entrées conseillées** : Particules avec poses (import CryoSPARC du raffinement consensus, sélection, particules soustraites), la carte consensus non affûtée comme référence et le masque de la région (dessiné sur la carte consensus).

**Conseils**

- Les demi-jeux CryoSPARC (alignments3D/split) sont conservés : la FSC reste gold-standard.
- Échantillonnage 1,8° avec recherches locales dès le départ (± 11°) : le réglage standard d'un raffinement local.
- Blush (RELION 5) améliore nettement les petites régions flexibles ; il demande le modèle Blush installé.
- Toutes les cartes sortent sur la grille de la carte consensus, même après recentrage : elles se superposent au modèle et servent directement à la carte composite.
- MPI : au moins 3 processus (un maître + un par demi-jeu) ; avec des GPU, 2 processus par GPU + 1.

**Étapes suivantes** : RELION signal subtraction, Composite map (focused maps), LocScale 2, ModelAngelo build, Local resolution

### RELION post-processing

`relion_postprocess` — logiciel : `relion`

Post-traitement RELION de deux demi-cartes : FSC masquée corrigée par randomisation de phase, affûtage par facteur B (ajustement de Guinier automatique ou donné), résolution locale en option.

**Quand l'utiliser**

- Rapporter une résolution et un facteur B « à la RELION » pour n'importe quelles demi-cartes.
- Carte affûtée de référence pour comparer avec LocScale, DeepEMhancer ou EMReady.

**À éviter / pièges**

- Demi-cartes non indépendantes (après spIsoNet, carte composite) : la FSC serait surestimée.

**Entrées conseillées** : Les demi-cartes non filtrées et un masque doux sur la même grille.

**Conseils**

- Facteur B automatique : ajustement à partir de 10 Å, sans objet sous ~8 Å de résolution.
- Le fichier MTF du détecteur (option avancée) corrige l'affûtage aux hautes fréquences.

**Étapes suivantes** : LocScale 2, ModelAngelo build, Deposition package

### RELION reconstruction

`relion_reconstruct` — logiciel : `relion`

Reconstruit les demi-cartes et la carte complète de particules à partir de leurs poses, sans alignement (relion_reconstruct), et calcule la FSC : la carte pleine résolution d'un sous-ensemble.

**Quand l'utiliser**

- Sous-ensemble sélectionné dans l'espace latent de cryoDRGN : carte à pleine résolution de cet état.
- Vérifier rapidement des particules converties depuis CryoSPARC (même carte que le consensus ?).

**À éviter / pièges**

- Particules sans demi-jeux : ils sont tirés au hasard et la FSC est optimiste (les deux moitiés ont été alignées ensemble) ; un raffinement local donne une vraie FSC gold-standard.

**Entrées conseillées** : Particules avec poses (import CryoSPARC, sélection cryoDRGN, sortie d'un job RELION) ; masque facultatif pour la FSC masquée.

**Conseils**

- Les demi-jeux de CryoSPARC (alignments3D/split) sont repris quand ils existent.

**Étapes suivantes** : RELION local refinement, Half-map FSC, LocScale 2

### RELION signal subtraction

`relion_subtract` — logiciel : `relion`

Soustraction de signal (relion_particle_subtract) : retire de chaque image la projection de tout ce qui est hors du masque, recentre les particules sur la région gardée et les recadre dans une boîte plus petite. Option : expansion de symétrie préalable (chaque copie d'un complexe symétrique devient une particule).

**Quand l'utiliser**

- Raffinement ou classification focalisés d'une région que le reste du complexe empêche d'aligner.
- Complexe symétrique dont une copie varie : expansion de symétrie + soustraction, puis classification en C1.
- Préparer une analyse cryoDRGN d'une seule région (petite boîte = entraînement plus rapide).

**À éviter / pièges**

- Particules qui n'ont pas été raffinées dans RELION : la soustraction utilise le modèle RELION du raffinement (lancez d'abord « RELION local refinement » sur tout le complexe, recherches locales, quelques heures).
- Masque trop serré : le signal résiduel de la région serait soustrait ; dilatez-le de quelques Å.

**Entrées conseillées** : Les particules de « RELION local refinement » (tout le complexe) et le masque doux de la région à garder.

**Conseils**

- Boîte automatique : 1,5 × l'étendue du masque (place pour le signal délocalisé par la CTF).
- Le recentrage se fait par voxels entiers : les cartes reviennent exactement sur la grille consensus.
- Les sorties « map » et « mask » reprennent la carte du raffinement et le masque : le job suivant se remplit tout seul.
- Expansion de symétrie : × n particules (C3 → 3 copies), même demi-jeu pour les copies d'une particule.

**Étapes suivantes** : RELION local refinement, RELION 3D classification (focused), cryoDRGN training

## Traitement de carte

### Align to symmetry axes (RELION)

`relion_align_symmetry` — logiciel : `relion`

Tourne (et recentre) une carte raffinée sans symétrie pour que ses axes de symétrie soient ceux de RELION (relion_align_symmetry), comme les « Volume Alignment Tools » de CryoSPARC : la référence d'un raffinement symétrique ou d'une expansion de symétrie. La symétrie peut aussi être appliquée.

**Quand l'utiliser**

- Après un raffinement C1 qui montre une symétrie : aligner la carte avant de raffiner avec la symétrie.
- Avant une expansion de symétrie, avec une carte venue d'un autre logiciel ou d'une autre convention.

**À éviter / pièges**

- Une symétrie supposée sans la voir dans la carte C1 : vérifiez d'abord (classes 3D, carte C1).

**Entrées conseillées** : Une carte (raffinée en C1, de préférence non affûtée).

**Conseils**

- Boîte de travail 64 px (Nyquist ~20 Å) : suffisant et rapide.
- Puis 3D auto-refine avec cette carte et la symétrie (filtre initial 10-15 Å : la carte vient de ces particules).
- « Rotation about z only » quand l'axe principal est déjà sur z (Cn).

**Étapes suivantes** : 3D auto-refine (RELION), Symmetry expansion (RELION), Mask creation (RELION)

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

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), ISOLDE session

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
- Phenix 2 remplace auto_sharpen par map_sharpening : CryoPlug le lance avec --force, il fonctionne toujours.

**Étapes suivantes** : ModelAngelo build, Fit in map (rigid body), Rigid-body fit (ChimeraX)

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

Crée un masque à bord doux : à partir de la densité (passe-bas + seuil), des atomes de chaînes choisies, de la carte simulée à partir de ces atomes (comme molmap dans Chimera/ChimeraX), ou de la densité proche de ces atomes. Dilatation et bord doux en Å, comme dans CryoSPARC ; combinaison avec un autre masque (union, soustraction, intersection).

**Quand l'utiliser**

- Masque de raffinement local d'une sous-partie d'un gros complexe (chaînes choisies + densité voisine).
- Masque lisse d'un domaine à partir du modèle (« model map ») : la recette molmap + seuil + bord doux de Chimera, sans quitter CryoPlug ; la carte simulée est aussi donnée (sortie « model_map »).
- Masque FSC, LocScale ou spIsoNet quand celui de CryoSPARC manque ou ne convient pas.
- Soustraction de signal : masque du complexe moins la région à garder (« subtract »).
- Masque à fournir à l'EMDB avec la carte déposée.

**À éviter / pièges**

- Masque trop serré (dilatation + bord < 6 Å) pour la FSC : il gonfle la résolution.
- Seuil automatique sans masse attendue sur une carte très bruitée : vérifiez la valeur et les coupes.

**Entrées conseillées** : Une carte (elle fixe la boîte et le pixel) ; un modèle ajusté pour les masques par chaînes ; un second masque pour les combinaisons.

**Conseils**

- Raffinement local : densité proche du modèle (6–8 Å), dilatation 3–5 Å, bord 8–12 Å.
- « Model map » : une gaussienne par atome (σ = résolution / (π√2), hauteur = numéro atomique, comme molmap) ; 10–15 Å et le contour à 95 % (celui de ChimeraX) donnent un masque lisse de domaine, à dilater de 2–3 Å.
- FSC / LocScale : densité filtrée à 15 Å, dilatation 3 Å, bord 6 Å.
- Donnez la masse attendue (kDa) pour un seuil automatique fiable ; sinon la masse des atomes choisis est utilisée.
- Les coupes du rapport montrent le masque (orange) sur la carte : vérifiez qu'il ne coupe pas de densité.
- Le chemin du fichier est donné pour l'importer dans CryoSPARC (Import 3D Volumes, type mask).

**Étapes suivantes** : Half-map FSC, LocScale 2, Anisotropy correction (spIsoNet), Local resolution, RELION local refinement, RELION signal subtraction

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

**Étapes suivantes** : Fit in map (rigid body), Rigid-body fit (ChimeraX), ISOLDE session

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

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), Real-space refinement (Phenix)

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

### EMReady2

`emready` — logiciel : `emready` · GPU

Amélioration de carte par apprentissage profond combinant informations locales et non locales.

**Quand l'utiliser**

- Alternative à DeepEMhancer/EMmerNet pour l'interprétation, notamment le tracé de la chaîne principale.

**À éviter / pièges**

- Mêmes limites que les autres réseaux : interprétation seulement, pas de validation ni de dépôt comme carte primaire.

**Entrées conseillées** : La carte (non affûtée ou affûtée), masque optionnel.

**Conseils**

- Un pas (stride) plus petit est plus précis mais plus lent.

**Étapes suivantes** : Fit in map (rigid body), Rigid-body fit (ChimeraX), ModelAngelo build

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

### Helical symmetry (RELION)

`relion_helix_toolbox` — logiciel : `relion`

Outils de symétrie hélicale de RELION (relion_helix_toolbox) sur la carte d'un filament : recherche locale de la torsion (twist) et de la montée (rise), puis imposition de la symétrie trouvée dans l'espace réel ; imposition d'une symétrie donnée ; ou cylindre comme référence initiale d'un raffinement hélical.

**Quand l'utiliser**

- Après un premier raffinement hélical : affiner twist et rise dans une petite fenêtre autour des valeurs connues (diffraction, classes 2D).
- Symétriser une carte de filament pour la construction de modèle.
- Pas de référence pour un raffinement hélical : un cylindre du diamètre du tube.

**À éviter / pièges**

- Une fenêtre de recherche large : la recherche est locale, plusieurs symétries peuvent expliquer une carte.
- Une valeur trouvée au bord de la fenêtre : élargissez la fenêtre (le job prévient).

**Entrées conseillées** : La carte d'un raffinement hélical (recherche, imposition) ; rien pour le cylindre.

**Conseils**

- Twist positif : hélice droite ; vérifiez la main avec un modèle ou des hélices α visibles.
- Partie centrale à 30 % de la hauteur de la boîte : les extrémités d'une reconstruction sont moins bonnes.
- Le cylindre se fait à la taille de pixel et à la boîte des particules du raffinement.

**Étapes suivantes** : 3D auto-refine (RELION), Mask creation (RELION), RELION post-processing

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

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), Fit in map (rigid body), Rigid-body fit (ChimeraX), ISOLDE session

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
- Phenix 2 remplace local_aniso_sharpen par map_sharpening : CryoPlug le lance avec --force.

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

Opérations simples sur une carte, comme les « Volume Tools » de CryoSPARC : taille de pixel (en-tête), rééchantillonnage (autre taille de pixel par recadrage ou remplissage de Fourier, ou sur la grille d'une autre carte), recadrage/agrandissement de boîte, inversion de main, B-factor global, filtre passe-bas, multiplication par un masque, normalisation.

**Quand l'utiliser**

- La main est fausse (hélices gauches, ModelAngelo ne construit que des fragments) : « Flip handedness ».
- Boîte non cubique ou trop grande pour un logiciel : recadrer en boîte cubique.
- Carte d'un autre jeu de données ou d'un autre logiciel à comparer : « Resample to pixel size » ou la grille d'une autre carte (entrée « Grid of »).
- Carte filtrée à une résolution donnée pour une figure ou un ajustement grossier.

**À éviter / pièges**

- Recadrer la carte déposée sans recadrer de même les demi-cartes et le masque.
- Agrandir le pixel au-delà de la moitié de la résolution : les détails au-delà de Nyquist sont perdus.

**Entrées conseillées** : Une carte ; le masque seulement pour « Multiply by mask » ; une carte de référence pour sa grille.

**Conseils**

- Les opérations s'enchaînent dans un ordre fixe : pixel → rééchantillonnage → boîte → main → B-factor → filtre → masque → normalisation.
- « Set pixel size » ne change que l'en-tête (calibration fausse) ; « Resample » change la grille.
- B-factor négatif = affûtage, positif = flou.

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), Fit in map (rigid body), Rigid-body fit (ChimeraX)

### Map sharpening (Phenix)

`phenix_map_sharpening` — logiciel : `phenix`

L'outil d'affûtage de Phenix 2 (phenix.map_sharpening, qui remplace auto_sharpen et local_aniso_sharpen) : mise à l'échelle par coquilles à partir des deux demi-cartes (ou d'une carte et d'un modèle ajusté), anisotrope et éventuellement locale ; ou affûtage par facteur B d'une seule carte.

**Quand l'utiliser**

- Phenix 2.x : la méthode actuelle de Phenix pour affûter une carte avant la construction ou l'affinement.
- Carte anisotrope (orientation préférentielle modérée) : la correction anisotrope est faite d'office.
- Comparer à LocScale, DeepEMhancer ou au post-traitement RELION sur la même carte.

**À éviter / pièges**

- Une seule carte sans modèle en mode « shell » : impossible, choisissez « b-factor ».
- Affûtage local sur une carte pleine taille : très long ; découpez d'abord la région (Map operations).

**Entrées conseillées** : Les demi-cartes non filtrées (recommandé) ; sinon la carte et un modèle ajusté ; la séquence aide Phenix à estimer le volume de la molécule.

**Conseils**

- La résolution est facultative en mode « shell », obligatoire en « b-factor ».
- Avec Phenix 1.21 ou plus ancien sans map_sharpening : job « Auto-sharpen (Phenix) ».

**Étapes suivantes** : ModelAngelo build, CryoAtom2 build, CryoAtom2 enhanced (best of several runs), Real-space refinement (Phenix), Local resolution

### Mask creation (RELION)

`relion_mask_create` — logiciel : `relion`

relion_mask_create : la carte filtrée passe-bas, seuillée, dilatée de quelques pixels et adoucie par un bord en cosinus. Le masque de solvant du post-traitement, du raffinement et de la CTF refinement.

**Quand l'utiliser**

- Après le premier raffinement 3D, pour le post-traitement et les raffinements suivants.

**À éviter / pièges**

- Seuil trop haut : le masque coupe de la densité, la FSC est surestimée.
- Carte affûtée en entrée : prenez la carte non affûtée du raffinement.

**Entrées conseillées** : Une carte raffinée non affûtée.

**Conseils**

- Filtre 15 Å, extension 3-5 px, bord doux 6-10 px.
- Seuil 0 = automatique (seuil d'Otsu de la carte filtrée) ; vérifiez le masque sur la carte dans le visualiseur.
- Masque dessiné à la main ou tiré d'un modèle : « Mask drawn in the 3D viewer » ou « Create mask ».
- Filaments : « Helical processing » garde la partie centrale le long de l'axe (z %).

**Étapes suivantes** : 3D auto-refine (RELION), CTF refinement (RELION), Bayesian polishing (RELION), RELION post-processing, Local resolution (RELION)

### Mask drawn in the 3D viewer

`draw_mask` — intégré (aucun logiciel externe)

Masque dessiné à la main sur une carte dans le visualiseur 3D, comme dans Chimera : sphères, boîtes et cylindres placés à la souris, région de densité connexe cliquée au niveau de contour, zone autour de chaînes, carte simulée à partir de chaînes d'un modèle (molmap), densité au-dessus d'un niveau, ajoutés, effacés ou intersectés ; puis dilatation et bord doux. Le job calcule le masque sur la grille complète de la carte, avec son centre de masse et la boîte pour recentrer les particules.

**Quand l'utiliser**

- Masque de raffinement local ou de soustraction d'une région qu'aucun modèle ne décrit encore.
- Retoucher un masque automatique : effacer une densité parasite, ajouter un domaine flexible.
- Masque de classification focalisée autour d'un site (ligand, domaine mobile).

**À éviter / pièges**

- Masque FSC global : « Create mask » (densité filtrée + seuil) est plus reproductible.
- Bord trop dur pour un raffinement : gardez dilatation + bord doux ≥ 6-8 Å.

**Entrées conseillées** : La carte sur laquelle dessiner (elle fixe la boîte et le pixel : prenez la carte consensus pour les jobs RELION) ; un modèle ajusté pour les zones autour de chaînes et les cartes simulées (molmap).

**Conseils**

- Visualiseur 3D → bouton « Mask » : choisissez un outil, cliquez sur la carte ; la surface orange est le masque.
- « Region » prend toute la densité connexe au niveau de contour (réglez le niveau, ou un passe-bas pour lisser).
- « Model map » : la carte simulée de chaînes d'un modèle ouvert, coupée au contour qui englobe 95 % de sa densité ; ou la commande ChimeraX « molmap #2/A,B 10 » dans la ligne de commande du visualiseur.
- « At the centre of the view » : cliquez d'abord un atome pour centrer la vue dessus, puis placez la forme.
- Ctrl + Z annule, Suppr retire l'opération sélectionnée ; les curseurs modifient l'opération sélectionnée.
- Un job créé dans un workflow attend le dessin : « Draw in 3D » sur ce job, puis « Save as job » le lance.

**Étapes suivantes** : RELION signal subtraction, RELION local refinement, RELION 3D classification (focused), Half-map FSC, LocScale 2

## Hétérogénéité (variabilité 3D)

### 3D multi-body refinement (RELION)

`relion_multibody` — logiciel : `relion`

Raffine séparément des corps rigides d'un complexe flexible, chacun avec son masque, à partir d'un raffinement consensus (relion_refine --multibody_masks), puis analyse leurs mouvements (relion_flex_analyse) : les mouvements principaux en séries de cartes à jouer dans le visualiseur.

**Quand l'utiliser**

- Complexe fait de domaines rigides qui bougent les uns par rapport aux autres.

**À éviter / pièges**

- Corps trop petits (moins de 100-150 kDa) : leur alignement devient instable.
- Flexibilité continue sans domaines rigides : DynaMight ou cryoDRGN.

**Entrées conseillées** : Les particules d'un « 3D auto-refine (RELION) » et deux à six masques doux de corps dessinés sur la carte consensus.

**Conseils**

- Le job reprend la dernière itération du raffinement aux demi-jeux séparés (RELION refuse l'itération finale, où ils sont réunis).
- Les plus grands corps d'abord ; priors de 10° et 2 Å par défaut.
- L'analyse en composantes principales donne la part de variance de chaque mouvement.

**Étapes suivantes** : Subset selection (RELION), 3D auto-refine (RELION), Composite map (focused maps)

### DynaMight flexibility (RELION)

`relion_dynamight` — logiciel : `relion` · GPU

Apprend les déformations continues des particules à partir d'un modèle de gaussiennes de la carte consensus (DynaMight, RELION 5), puis, au choix, les déformations inverses et une rétroprojection déformée : une carte consensus améliorée là où la particule bouge.

**Quand l'utiliser**

- Flexibilité continue qui floute la carte consensus.

**À éviter / pièges**

- Sans GPU : très lent.
- Particules sans poses raffinées : DynaMight part d'un raffinement consensus.

**Entrées conseillées** : Les particules raffinées et la carte consensus (même boîte et même taille de pixel).

**Conseils**

- Nombre de gaussiennes : 1 à 2 par résidu de la structure.
- L'exploration de l'espace latent est une fenêtre interactive à lancer à la main (la commande est dans le rapport).

**Étapes suivantes** : RELION post-processing, LocScale 2, Local resolution (RELION)

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

**Étapes suivantes** : Fit in map (rigid body), Rigid-body fit (ChimeraX), ISOLDE session, Map-model validation (Q-score, FSC)

### Select particles (latent space)

`select_particles` — intégré (aucun logiciel externe)

Garde ou retire des particules choisies dans un espace latent (cryoDRGN ou 3D variability) : des clusters entiers, une région dessinée au lasso, ou les particules aberrantes, loin des autres. Écrit la sélection et les particules écartées (.cs pour CryoSPARC ou .star pour RELION, et les indices pour cryoDRGN).

**Quand l'utiliser**

- Des clusters ont des volumes de junk (weak, noisy, blurry dans la galerie) : retirez-les puis réentraînez.
- Une région de l'espace latent ou un état intéressant : gardez ses particules et raffinez-les dans CryoSPARC.
- Des particules aberrantes (‖z‖ très grand) : retirez-les.

**À éviter / pièges**

- Sélectionner sur un modèle non convergé : les clusters peuvent encore changer.

**Entrées conseillées** : Un espace latent (cryoDRGN ou 3D variability).

**Conseils**

- Le plus simple : depuis l'explorateur, « Keep… » / « Remove… » (clusters), « Keep region… » / « Remove region… » (lasso) ou « Remove outliers… » préparent le job.
- Les particules écartées sont aussi en sortie (excluded) : une classification 2D dans CryoSPARC confirme qu'il s'agit bien de junk.
- Un nouvel entraînement cryoDRGN sur la sélection réutilise les images déjà réduites ; en mode auto il prend le grand réseau (second tour du protocole).
- Outliers : ‖z‖ au-delà de la moyenne + 2 écarts-types (« Outlier threshold »).
- Dans CryoSPARC : Import Particle Stack avec le fichier .cs produit, puis Homogeneous / NU refinement pour valider un état.
- Espace latent cryoDRGN-AI : la sélection garde les poses apprises (« Poses of the selection ») ou, au choix, les poses consensus des particules d'origine, pour un entraînement cryoDRGN classique quand elles concordent.

**Étapes suivantes** : cryoDRGN training, cryoDRGN-AI ab initio reconstruction, cryoDRGN input check (backprojection)

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

**Étapes suivantes** : Extract volume from series, Select particles (latent space)

### cryoDRGN analysis

`cryodrgn_analyze` — logiciel : `cryodrgn` · GPU

Analyse à nouveau un modèle cryoDRGN entraîné : autre époque, plus de clusters, trajectoires plus longues, main inversée… Même explorateur, mêmes volumes et diagnostics que l'entraînement.

**Quand l'utiliser**

- Échantillonner plus finement l'espace latent (50 à 100 volumes) pour voir des états rares.
- Les dernières époques donnent des volumes bruités (sur-apprentissage) : analysez l'époque indiquée par le contrôle de convergence.

**À éviter / pièges**

- Relancer un entraînement complet pour seulement changer le nombre de volumes.

**Entrées conseillées** : L'espace latent d'un entraînement cryoDRGN.

**Conseils**

- Epoch = 0 reprend l'époque de l'entrée (la dernière de l'entraînement).
- Volume box : volumes plus petits, plus rapides à générer et à visualiser ; la taille de pixel est corrigée. Les modèles cryoDRGN-AI restent à la taille d'entraînement (cryoDRGN ne les réduit pas).
- Modèle cryoDRGN-AI : « Flip handedness » change la main des volumes et des poses suivantes (elle est gardée dans le repère du modèle).

**Étapes suivantes** : Select particles (latent space), cryoDRGN trajectory, cryoDRGN landscape analysis, Volume series analysis

### cryoDRGN continue training

`cryodrgn_continue` — logiciel : `cryodrgn` · GPU

Poursuit l'entraînement d'un modèle cryoDRGN depuis son dernier point de sauvegarde (même réseau, mêmes particules), puis l'analyse à nouveau.

**Quand l'utiliser**

- Le contrôle de convergence répond « not converged » ou « nearly converged » : le protocole passe de 50 à 100 époques.
- Les volumes changent encore d'une époque à l'autre.

**À éviter / pièges**

- Changer de taille d'image, de réseau ou de particules : lancez un nouvel entraînement.
- Prolonger un entraînement déjà convergé : risque de sur-apprentissage (volumes plus bruités).

**Entrées conseillées** : L'espace latent d'un entraînement cryoDRGN ou cryoDRGN-AI (ou d'une continuation précédente).

**Conseils**

- « Train until epoch » = nombre total d'époques à la fin (0 = le double).
- Modèle cryoDRGN-AI : la continuation ajoute des époques de descente de gradient (cryodrgn abinit --load), puis refait la comparaison avec le consensus, les particules avec leurs poses apprises et l'analyse.
- Les époques précédentes restent dans le dossier du premier job : les courbes et le contrôle de convergence couvrent tout l'entraînement.

**Étapes suivantes** : cryoDRGN convergence check, Select particles (latent space), cryoDRGN landscape analysis

### cryoDRGN convergence check

`cryodrgn_convergence` — logiciel : `cryodrgn` · GPU

Dit si un entraînement cryoDRGN a convergé, comme le recommande le protocole de Kinman et al. (2023) : courbe de perte, déplacement des particules dans l'espace latent d'une époque à l'autre, stabilité de leurs voisins, et évolution des volumes de particules représentatives au fil des époques. Donne l'époque à partir de laquelle les résultats sont stables, ou conseille de prolonger.

**Quand l'utiliser**

- Après l'entraînement final, avant d'interpréter des détails.
- Choisir l'époque à analyser quand les dernières époques semblent sur-apprises.

**À éviter / pièges**

- Entraînement avec un seul point de sauvegarde (« Checkpoint every » trop grand).

**Entrées conseillées** : L'espace latent d'un entraînement cryoDRGN (après une continuation, toutes les époques sont comparées).

**Conseils**

- Verdict en tête du rapport (converged, nearly converged, not converged) avec la marche à suivre.
- Les sorties « Particle … across epochs » se jouent en 3D : un entraînement convergé montre le même volume à chaque époque.
- L'explorateur montre l'espace latent aux époques comparées, aligné sur la dernière ; « Play the epochs » anime la mise en place des états.
- Une perte qui baisse encore lentement n'empêche pas la convergence : ce sont les volumes et l'organisation de l'espace latent qui décident.
- Modèle cryoDRGN-AI : la stabilité des poses entre points de sauvegarde entre dans le verdict, la perte est jugée sur les époques SGD et l'époque 0 (pré-entraînement) est ignorée.

**Étapes suivantes** : cryoDRGN continue training, cryoDRGN analysis, Select particles (latent space), cryoDRGN landscape analysis

### cryoDRGN input check (backprojection)

`cryodrgn_backproject` — logiciel : `cryodrgn` · GPU

Vérifie que cryoDRGN lit correctement les particules avant un long entraînement (contrôle du protocole de Kinman et al. 2023) : conversion des poses et du CTF, réduction des images, puis rétroprojection des 10 000 premières particules en une carte à comparer au raffinement consensus.

**Quand l'utiliser**

- Avant le premier entraînement sur un nouveau jeu de particules (quelques minutes).
- Des volumes cryoDRGN creux, bruités ou sans rapport avec la carte consensus : vérifier les entrées.
- Particules venant d'un .star ou d'un job inhabituel (raffinement hétérogène, ab initio).

**À éviter / pièges**

- Juger la qualité des données sur cette carte : c'est une simple rétroprojection de quelques milliers d'images, pas une reconstruction raffinée.

**Entrées conseillées** : Les particules d'un raffinement consensus (Import from CryoSPARC, Import particles, ou une sélection).

**Conseils**

- Verdict « inputs read correctly » : la carte ressemble au consensus (même forme, même main) et la FSC entre demi-lots montre du signal au-delà des premières coquilles.
- Densité « negative » : le contraste est l'inverse de ce qu'attend cryoDRGN. Cochez « Do not invert the images » pour l'entraînement, sinon les volumes sortent creux.
- Résolution très mauvaise : poses mal lues. Vérifiez « Poses from » (refinement, heterogeneous refinement, ab initio) et que les particules viennent bien du raffinement consensus.
- L'entraînement cryoDRGN fait ce contrôle tout seul (paramètre « Input check ») : ce job sert à le faire à part, ou à tester l'autre convention de contraste.
- Les particules préparées (images réduites, poses, CTF) sont réutilisées par un entraînement à la même taille.

**Étapes suivantes** : cryoDRGN training

### cryoDRGN landscape analysis

`cryodrgn_landscape` — logiciel : `cryodrgn` · GPU

Cartographie le paysage conformationnel à partir des volumes plutôt que de l'espace latent (cryodrgn analyze_landscape) : un grand nombre de volumes générés dans tout l'espace latent sont comparés par ACP dans un masque, puis regroupés en états par classification hiérarchique.

**Quand l'utiliser**

- Après un entraînement final convergé : définir des états discrets et leurs particules.
- L'UMAP est difficile à lire : des particules éloignées dans l'espace latent peuvent avoir le même volume.
- Avec un masque sur un domaine mobile : décrire précisément son mouvement.

**À éviter / pièges**

- Premier passage de nettoyage (128 px) : inutilement coûteux.
- Particules où le junk n'a pas été retiré : il forme des états à part.

**Entrées conseillées** : L'espace latent d'un entraînement cryoDRGN ; un masque facultatif (zone à comparer).

**Conseils**

- 1000 volumes et 10 états (valeurs par défaut de cryoDRGN) ; moins de volumes pour un essai rapide.
- La sortie « latent » porte les états à la place des clusters : l'explorateur (Keep…) ou « Select particles » prennent les particules d'un état, à raffiner dans CryoSPARC.
- Trajectoires « Volume PC » : les principales façons dont les volumes diffèrent dans le masque.
- Modèle cryoDRGN-AI : cryoDRGN inverserait ses volumes, puis effacerait la densité négative (la molécule) avant de les comparer ; CryoPlug le lance sur une copie du modèle qui ne les inverse pas, à la taille d'entraînement, et remet les volumes dans le repère du consensus avec la bonne taille de pixel.

**Étapes suivantes** : Select particles (latent space), Volume series analysis, Extract volume from series, cryoDRGN trajectory

### cryoDRGN training

`cryodrgn_train` — logiciel : `cryodrgn` · GPU

Reconstruction hétérogène avec cryoDRGN : un réseau de neurones apprend un espace latent des conformations et compositions des particules (poses du raffinement consensus). Le job contrôle d'abord les entrées, suit les pertes en direct, puis analyse l'espace latent : explorateur interactif, volumes des clusters avec alertes « junk », trajectoires, diagnostics et conseils pour la suite.

**Quand l'utiliser**

- La carte consensus a des régions floues qui pourraient bouger ou être partiellement occupées.
- Chercher des états minoritaires, des sous-unités absentes d'une partie des particules, des mouvements continus.
- Nettoyer les particules : repérer les clusters de « junk » et les retirer.

**À éviter / pièges**

- Particules sans poses fiables : faites d'abord un bon raffinement consensus (NU-refine), ou utilisez cryoDRGN-AI (ab initio), qui apprend les poses.
- Particules déjà très filtrées (classifications 2D/3D agressives) : l'hétérogénéité cherchée a pu disparaître.
- Lire les volumes cryoDRGN comme des cartes haute résolution : ils montrent les états, ils ne servent pas à affiner un modèle fin.

**Entrées conseillées** : Les particules d'un raffinement consensus (Import from CryoSPARC les importe avec les cartes), ou une sélection faite dans un espace latent précédent.

**Conseils**

- Protocole en deux tours (Kinman et al. 2023) : 1) 128 px, petit réseau (256 × 3), z = 8, 50 époques, sur toutes les particules, pour repérer le junk ; 2) 256 px, grand réseau (1024 × 3), sur les particules nettoyées. « Network size » en mode auto suit ce protocole (grand réseau pour une sélection d'un espace latent précédent).
- Courbes de perte en direct : la perte de reconstruction baisse puis se stabilise. La carte du job affiche le temps par époque et le temps restant.
- Galerie des volumes : les clusters marqués weak, noisy ou blurry sont souvent du junk. Regardez-les en 3D (double-clic) avant de les retirer.
- Tableau « Is the latent space structural? » : si le défocus ou l'orientation sont fortement codés (R² > 0,3), une partie de l'espace latent reflète les images et non la structure.
- Explorateur : « Colour by » (densité, ‖z‖, défocus, orientation…), lasso pour une région, clic sur une particule pour son volume, « Particle images » pour voir les images d'un cluster.
- Le chargement paresseux (--lazy) s'active seul si les images ne tiennent pas en mémoire. Plusieurs GPU (--multigpu) : utile à 256 px avec le grand réseau.
- CUDA out of memory : baissez le batch size. Erreur d'assertion pendant l'entraînement : décochez « Mixed precision ».

**Étapes suivantes** : cryoDRGN convergence check, Select particles (latent space), cryoDRGN continue training, cryoDRGN analysis, cryoDRGN landscape analysis, cryoDRGN trajectory, Volume series analysis

### cryoDRGN trajectory

`cryodrgn_trajectory` — logiciel : `cryodrgn` · GPU

Génère les volumes le long d'un chemin de l'espace latent entre des clusters (ou des particules) choisis : à travers les particules (chemin soutenu par les données, cryodrgn graph_traversal) ou en ligne droite. Un film de la transition à jouer dans le visualiseur.

**Quand l'utiliser**

- Visualiser le passage d'un état à un autre repéré dans l'explorateur latent.
- Préparer une figure ou une vidéo de mouvement pour l'article.

**À éviter / pièges**

- Interpréter un chemin en ligne droite qui traverse des régions vides de particules : préférez le chemin à travers les particules. Un chemin n'est pas forcément un chemin biologique.

**Entrées conseillées** : L'espace latent d'un entraînement ou d'une analyse cryoDRGN, et les numéros de clusters à relier.

**Conseils**

- Dans l'explorateur, cliquez les clusters dans l'ordre du chemin puis « Trajectory… » : le job est préparé.
- Particules au lieu de clusters (paramètre avancé) : relier deux particules précises (numéros affichés par l'explorateur). « Back to the start » ferme la boucle (mouvement cyclique).
- Si le graphe des voisins est coupé entre les deux points, la ligne droite est utilisée (avertissement dans le log).
- Volume series analysis sur la trajectoire montre où la densité change.

**Étapes suivantes** : Volume series analysis, Extract volume from series

### cryoDRGN volumes at particles

`cryodrgn_volumes` — logiciel : `cryodrgn` · GPU

Génère le volume de particules choisies (à partir de leurs coordonnées latentes), ou d'une région dessinée au lasso dans l'explorateur : le volume au centre de la région, ou plusieurs volumes répartis dedans.

**Quand l'utiliser**

- Une zone de l'espace latent intrigue mais n'a pas de centre de cluster.
- Voir à quoi ressemblent les particules d'une région avant de la garder ou de la retirer.

**À éviter / pièges**

- Générer des centaines de volumes : utilisez « cryoDRGN analysis » (k-means) ou « landscape analysis ».

**Entrées conseillées** : L'espace latent d'un entraînement ou d'une analyse cryoDRGN.

**Conseils**

- Depuis l'explorateur : cliquez une particule puis « Volume of particle … », ou dessinez une région au lasso puis « Volume of the region… ».
- Plusieurs volumes dans une région = centres k-means de ses particules (points sur les données, comme cryoDRGN).

**Étapes suivantes** : Volume series analysis, Extract volume from series

### cryoDRGN-AI ab initio reconstruction

`cryodrgn_abinit` — logiciel : `cryodrgn` · GPU

Reconstruction hétérogène ab initio avec cryoDRGN-AI (cryodrgn abinit, Levy et al. 2025) : la pose et les coordonnées latentes de chaque particule sont apprises ensemble, à partir des seules images et de leur CTF, sans raffinement consensus. CryoPlug ajoute le contrôle des entrées, le suivi par phase avec temps restant, la stabilité des poses, la comparaison avec les poses consensus (main, repère, écart par particule), une rétroprojection de validation, les particules réécrites avec leurs poses apprises et les outils d'espace latent des entraînements cryoDRGN.

**Quand l'utiliser**

- Le consensus est flou sur une partie de l'objet (grand mouvement, composition variable) : ses poses sont alignées sur la partie dominante et faussent un entraînement cryoDRGN classique.
- Travailler tôt, sur un jeu peu filtré (juste après une classification 2D), pour ne rien jeter trop vite : cryoDRGN-AI tolère le junk et le regroupe dans l'espace latent.
- Mélanges, états rares : l'article a trouvé un nouvel état de la V-ATPase et un supercomplexe rare de l'ankyrine-1.
- Contre-expertise d'un entraînement cryoDRGN : deux estimations indépendantes des poses ; si le même paysage apparaît, l'hétérogénéité n'est pas un artefact d'alignement.

**À éviter / pièges**

- Chercher la meilleure résolution : cryoDRGN-AI découvre, trie et donne des poses ; la carte finale de chaque état se fait dans CryoSPARC.
- Très petites particules (moins de ~250 kDa, le plus petit complexe de l'article) : la recherche de poses devient fragile.
- Particules symétriques dont on veut exploiter la symétrie : cryoDRGN-AI reconstruit en C1.
- cryoDRGN avant 4.2 : la commande abinit n'existe pas (et eval_vol ne lit ces modèles qu'à partir de 4.3).

**Entrées conseillées** : Des particules avec leur CTF : Import from CryoSPARC (ou Import particles) d'un job Select 2D, d'une extraction ou d'un raffinement. Si elles portent des poses consensus, elles sont comparées aux poses apprises. Sans poses consensus, une carte de référence facultative (même molécule) fixe le repère et la main.

**Conseils**

- Réglages de l'article : images à 128 px, z = 8, réseau 3 × 256, 3 époques de recherche de poses puis descente de gradient (30 époques au total par défaut, 50 à 100 pour de gros jeux).
- Les époques de recherche de poses (HPS) sont lentes, les époques SGD environ 20 fois plus rapides : la carte du job affiche la phase et une estimation du temps restant (« roughly » tant que la vitesse SGD n'est pas mesurée).
- Contrôle des entrées : image moyenne des particules (elles doivent être sombres dans les images brutes), plus une rétroprojection avec les poses consensus si elles existent. « Image contrast = auto » choisit seul --uninvert-data : un mauvais choix donne des volumes creux.
- « Pose changes between checkpoints » : grandes pendant la recherche de poses, puis quelques degrés. Des valeurs faibles et stables à la fin indiquent des poses convergées.
- Comparaison avec le consensus : main (un ab initio peut sortir en miroir, c'est alors corrigé), écart médian, % de particules à moins de 10°. Coloriez l'explorateur par « Pose difference from the consensus » : des clusters à grand écart sont du junk ou une autre espèce.
- « Put the results in the consensus frame » : volumes et poses dans le repère et la main du consensus (ou de la carte de référence) ; les volumes se superposent à la carte consensus dans le visualiseur.
- Sortie « Particles with their learned poses » (.cs ou .star) : s'importe tel quel dans CryoSPARC (Import Particle Stack, reconstruction sans réalignement), ou sert à un entraînement cryoDRGN classique.
- Validation : la FSC de la rétroprojection avec les poses apprises dit si la recherche de poses a réussi.
- Second tour (comme dans l'article) : retirez le junk dans l'explorateur, puis relancez cryoDRGN-AI sur la sélection (les images réduites sont réutilisées).
- CUDA out of memory pendant la recherche de poses : « Batch size (pose search) » à 8.

**Étapes suivantes** : cryoDRGN convergence check, Select particles (latent space), cryoDRGN continue training, cryoDRGN analysis, cryoDRGN landscape analysis, cryoDRGN trajectory, cryoDRGN training, Volume series analysis

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

**Étapes suivantes** : Process predicted model (Phenix), Fit in map (rigid body), Rigid-body fit (ChimeraX)

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

**Étapes suivantes** : Process predicted model (Phenix), Fit in map (rigid body), Rigid-body fit (ChimeraX), Dock in map (Phenix)

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

### CryoAtom2 enhanced (best of several runs)

`cryoatom_enhanced` — logiciel : `cryoatom` · GPU

CryoAtom2 lancé plusieurs fois, avec plusieurs réglages (et éventuellement d'autres cartes), chaque modèle noté contre la carte expérimentale ; le meilleur est gardé, ses trous comblés par les morceaux bien ajustés des autres essais, les chaînes identifiées et reconstruites jusqu'à convergence, la main vérifiée. En option, la carte est améliorée avec le modèle (LocScale…) puis reconstruite, tant que le score monte.

**Quand l'utiliser**

- Carte difficile (résolution locale basse, régions flexibles) : CryoAtom2 construit différemment à chaque lancement, et ses auteurs conseillent d'en lancer plusieurs et de garder le meilleur.
- Vous ne savez pas quels réglages conviennent : « auto » essaie les réglages standard et, selon la résolution, « more residues » (seuils Cα/P abaissés) ou « low resolution » (4 tours).
- Comparer la construction dans plusieurs cartes (affûtées autrement, LocScale, EMReady…) en les notant toutes contre la même carte expérimentale.
- Composition inconnue : avec une base de séquences, identification puis reconstruction automatiques.
- Carte hétérogène (régions bien et mal résolues) : la boucle carte–modèle (« Improve the map and build again », LocScale hybride avec les demi-cartes) affûte localement avec les parties fiables du modèle, puis reconstruit.

**À éviter / pièges**

- Un seul essai rapide suffit (carte nette à haute résolution) : « CryoAtom2 build » prend N fois moins de GPU.
- Carte d'un réseau de neurones utilisée comme carte de référence : les modèles seraient notés contre une densité déjà interprétée. Branchez la carte expérimentale en « Map » et les autres en « Other map ».

**Entrées conseillées** : Carte expérimentale (référence de la notation), séquence(s), masque ; demi-cartes non filtrées pour améliorer la carte ; cartes alternatives et bases de séquences optionnelles.

**Conseils**

- Le tableau « Runs compared » donne, pour chaque essai, les résidus construits, ajustés (Q-score par résidu ≥ Q attendu − 0,2), dupliqués, et le score : résidus ajustés, une densité tracée deux fois comptée une seule fois.
- Comptez « Runs » × réglages × durée d'un CryoAtom2 (≈ 30 min pour 5 000 résidus sur une A100) ; « Maximum runs » plafonne le premier tour.
- Si le job signale une main probablement inversée, retournez la carte (et les demi-cartes) avant d'aller plus loin : la carte miroir et son modèle sont en sortie.
- Boucle carte–modèle : seuls les résidus ajustés à la densité, avec un B uniforme, sont donnés à LocScale (qui affine leurs B avec REFMAC5 : CCP4 requis) ; LocScale hybride complète le reste par des pseudo-atomes. Le cycle s'arrête dès qu'il n'améliore plus le score contre la carte expérimentale : la carte améliorée ne sert jamais de référence, ce qui évite qu'un modèle se « confirme » lui-même.
- Les méthodes sans modèle (EMmerNet, LocScale model-free, DeepEMhancer, EMReady2, modification de densité Phenix) donnent une carte de plus à essayer, une seule fois ; cochez-en plusieurs pour les comparer.
- Affinez ensuite le meilleur modèle (real-space refine, ISOLDE) : les modèles bruts gardent des erreurs locales. La carte améliorée (sortie « map_improved ») aide à interpréter ; déposez la carte expérimentale comme carte principale.

**Étapes suivantes** : Real-space refinement (Phenix), ISOLDE session, Map-model validation (Q-score, FSC), Sequence register check (checkMySequence)

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

### Fit in map (rigid body)

`fit_in_map` — intégré (aucun logiciel externe)

Placement de corps rigide d'un modèle, d'une carte ou d'un masque dans une carte, intégré (sans ChimeraX), avec l'algorithme de « fitmap » de ChimeraX : à partir de la position réglée à la main dans le visualiseur 3D (Move), optimisation locale de la valeur moyenne de la carte aux atomes, ou de l'overlap / corrélation de la carte simulée du modèle à une résolution, ou recherche depuis des placements aléatoires.

**Quand l'utiliser**

- Placer un modèle prédit (AlphaFold, ColabFold, Boltz) ou un domaine dans la densité avant l'affinement.
- Superposer une carte de raffinement local, un masque ou une autre carte sur la carte consensus (sortie sur sa grille).
- Écrire la position d'un objet déplacé à la souris dans le visualiseur (« Save as job » de Move & fit).

**À éviter / pièges**

- Changement de conformation important : il faut de la flexibilité (ISOLDE, real-space refine avec morphing).
- Recherche globale d'un petit domaine dans une très grande carte : partez d'une position approchée (à la main) ou limitez le rayon de recherche.

**Entrées conseillées** : Le modèle (ou la carte / le masque) à placer, et la carte cible ; la position du visualiseur est remplie par « Save as job » dans Move & fit.

**Conseils**

- Résolution 0 : ajustement des atomes dans la densité (défaut de ChimeraX) ; 6-10 Å avec « correlation » pour un modèle encore loin de sa place ou une carte à basse résolution.
- Modèle à plus de 3-4 Å de sa place : une résolution de 6-8 Å (la carte simulée porte plus loin) ; « Then fit the atoms » donne ensuite la position exacte.
- Recherche : 50-100 placements aléatoires ; « Search radius » pour rester près de la position donnée.
- « Chains fitted » : le fit ne regarde que ces chaînes (un domaine, une sous-unité), mais tout le modèle est déplacé.
- Dans le visualiseur : commande « fitmap #2 inMap #1 resolution 8 », ou le bouton Fit de Move & fit.

**Étapes suivantes** : Real-space refinement (Phenix), Refinement (Servalcat), ISOLDE session, Map-model validation (Q-score, FSC), Merge models

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

**Étapes suivantes** : CryoAtom2 build, CryoAtom2 enhanced (best of several runs), ModelAngelo build

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

**Étapes suivantes** : Fit in map (rigid body), Rigid-body fit (ChimeraX), Dock in map (Phenix)

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

### Bayesian polishing (RELION)

`relion_polish` — logiciel : `relion`

Suit le mouvement de chaque particule à travers les images du film et la ré-extrait avec des poids par image ajustés aux données (relion_motion_refine) : le polissage bayésien. Entraîner une fois les paramètres sur un sous-ensemble (facultatif), puis polir ; raffiner ensuite les particules « shiny ».

**Quand l'utiliser**

- Après la CTF refinement, pour les derniers dixièmes d'Å.

**À éviter / pièges**

- Micrographies importées déjà sommées : il faut les films et la correction de mouvement de RELION.

**Entrées conseillées** : Les particules raffinées, les micrographies de la correction de mouvement (ou n'importe quelles micrographies qui en descendent : le mouvement y est retrouvé), les demi-cartes et un masque.

**Conseils**

- Entraînement : environ 10 000 particules suffisent ; branchez ses paramètres sur le job de polissage (entrée « Trained parameters »).
- Sans entraînement : sigmas par défaut 0,2 / 5000 / 2.
- Le facteur B par image montre la perte de signal au fil de l'exposition.

**Étapes suivantes** : 3D auto-refine (RELION)

### CTF refinement (RELION)

`relion_ctf_refine` — logiciel : `relion`

Affine l'optique du jeu de données et la CTF de chaque particule contre la carte raffinée (relion_ctf_refine) : grandissement anisotrope, tilt du faisceau, aberrations d'ordre 3 et 4 par groupe optique, puis défocus par particule et astigmatisme par micrographie. « Complete » enchaîne les trois passes du tutoriel de RELION.

**Quand l'utiliser**

- Résolution meilleure que ~4,5 Å : le gain est net.
- Tilt du faisceau (coma) ou grandissement anisotrope suspectés.

**À éviter / pièges**

- Carte à basse résolution (au-delà de 6 Å) : les ajustements sont mal contraints.

**Entrées conseillées** : Les particules d'un « 3D auto-refine (RELION) », ses demi-cartes et un masque doux.

**Conseils**

- Le job fait lui-même le post-traitement (FSC des demi-cartes) dont relion_ctf_refine a besoin.
- Raffinez ensuite les particules à nouveau : la résolution doit s'améliorer.
- Le tableau des groupes optiques montre les paramètres ajustés (tilt, Zernike, matrice de grandissement).

**Étapes suivantes** : 3D auto-refine (RELION), Bayesian polishing (RELION)

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
- N/Q/H flips s'ajoutent à la stratégie (run=…+nqh_flips) : ce n'est pas un paramètre à part pour Phenix.
- Si Phenix refuse le modèle (« Map and model are not aligned », symétrie différente de la carte), CryoPlug relance avec l'option que Phenix propose, avec un avertissement dans le log.
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

### Local resolution (RELION)

`relion_locres` — logiciel : `relion`

Résolution locale avec relion_postprocess --locres : FSC dans une sphère douce déplacée sur la carte. Donne la carte de résolution locale (pour colorer la carte dans le visualiseur) et la carte filtrée localement.

**Quand l'utiliser**

- Repérer les régions flexibles ou mal définies après un raffinement.

**À éviter / pièges**

- Demi-cartes non indépendantes (spIsoNet, carte composite) : la résolution locale serait surestimée.

**Entrées conseillées** : Les demi-cartes d'un raffinement ; un masque en option (statistiques dans le masque).

**Conseils**

- Facteur B : celui du post-traitement convient.
- « Local resolution » (Phenix) donne une estimation indépendante à comparer.

**Étapes suivantes** : LocScale 2, ModelAngelo build, 3D multi-body refinement (RELION)

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

### Curate particles (statistics)

`relion_particle_curate` — intégré (aucun logiciel externe)

Garde les particules dont les statistiques sont dans des limites, comme « Subset Particles by Statistic » de CryoSPARC, mais en interactif : probabilité de la meilleure pose (rlnMaxValueProbDistribution), échantillons significatifs, contribution à la log-vraisemblance, correction de normalisation, figure de mérite du picking, défocus, ajustement CTF, décalage de l'alignement, nombre de particules sur la micrographie et toute autre colonne RELION. Le job attend avec l'histogramme de chaque statistique (les barres comptent les particules que gardent les autres limites), une carte de densité de deux statistiques et des images de particules gardées et écartées. Ou bien il applique d'un coup les limites écrites, ou sépare les particules en groupes d'une statistique (mélange de gaussiennes).

**Quand l'utiliser**

- Après une classification 2D ou 3D : écarter les particules mal alignées (probabilité de la meilleure pose basse, beaucoup d'échantillons significatifs) avant le raffinement.
- Après un raffinement : retirer les particules à normalisation aberrante ou à faible contribution à la vraisemblance.
- Micrographies surchargées (agrégats) : limite sur le nombre de particules par micrographie.
- Séparer en deux populations nettes d'une statistique : mode « Clusters » (deux gaussiennes ou plus).

**À éviter / pièges**

- Des limites serrées sur une seule itération : regardez les images des particules écartées avant de valider.
- Comparer des statistiques entre jobs différents : la log-vraisemblance dépend du jeu, de la boîte et du modèle.

**Entrées conseillées** : Des particules ; celles d'une classification ou d'un raffinement RELION ont le plus de statistiques.

**Conseils**

- Glissez sur un histogramme pour une limite (double-clic : aucune) ; « N out » compte les particules qu'elle écarte.
- Les barres bleues tiennent compte des autres limites : on voit ce qu'une limite de plus retirerait vraiment.
- Les images se renouvellent après chaque changement (« Other particles » pour un autre tirage).
- Agents et scripts : mode « thresholds » avec, par ligne, « statistique minimum maximum » (- pour aucune), par exemple « max_prob 0.1 - ».
- Au-delà d'un million de particules, les graphiques montrent un échantillon ; les limites s'appliquent à toutes.

**Étapes suivantes** : 3D auto-refine (RELION), 2D classification (RELION), 3D classification (RELION), Particle sets (intersection, difference)

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

### Downsample particles

`relion_downsample` — intégré (aucun logiciel externe)

Réduit la taille des images de particules par recadrage de Fourier (comme « Downsample Particles » de CryoSPARC) : 2D et 3D plus rapides à un pixel plus gros. Écrit de nouvelles piles et un fichier de particules RELION avec la nouvelle boîte et le nouveau pixel ; poses et décalages (en Å) sont gardés.

**Quand l'utiliser**

- Particules extraites à pleine taille : classification 2D, modèle initial et 3D à pixel réduit.

**À éviter / pièges**

- Le raffinement final à pixel réduit : la résolution est limitée à deux fois le nouveau pixel (Nyquist).

**Entrées conseillées** : Des particules (RELION ou CryoSPARC).

**Conseils**

- Boîte paire ; nouveau pixel = ancien pixel × ancienne boîte / nouvelle boîte.
- Pour revenir à pleine taille, ré-extrayez les particules (extraction avec « Particles to re-extract »).

**Étapes suivantes** : 2D classification (RELION), 3D initial model (RELION), 3D classification (RELION)

### Join star files (RELION)

`relion_join` — logiciel : `relion`

Réunit deux à quatre jeux de particules, de micrographies ou de films en un seul (relion_star_handler --combine), en retirant les particules présentes deux fois.

**Quand l'utiliser**

- Plusieurs sessions de collecte traitées séparément.
- Réunir les particules de plusieurs classes 2D ou 3D.

**À éviter / pièges**

- Jeux de types différents (particules et micrographies) : impossible.

**Entrées conseillées** : Deux à quatre sorties du même type.

**Conseils**

- Les groupes optiques de chaque jeu sont conservés.

**Étapes suivantes** : 2D classification (RELION), 3D auto-refine (RELION), Subset selection (RELION)

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

### Optics groups (beam shift, file names)

`relion_optics_groups` — intégré (aucun logiciel externe)

Répartit films, micrographies ou particules en groupes d'optique (comme les « Exposure Group Utilities » de CryoSPARC) : par le décalage de faisceau (image shift) lu dans les fichiers XML d'EPU et regroupé, par une partie des noms de fichiers, ou par ordre d'acquisition. Le CTF refinement ajuste ensuite le beam tilt et les aberrations de chaque groupe.

**Quand l'utiliser**

- Collecte avec image shift (AFIS) : chaque trou d'un motif a son propre beam tilt.
- Plusieurs sessions ou grilles dans un même jeu : un groupe par session.

**À éviter / pièges**

- Des groupes de quelques centaines de particules seulement : les aberrations s'estiment mal.

**Entrées conseillées** : Films, micrographies ou particules RELION ; pour le beam shift, le dossier des XML d'EPU.

**Conseils**

- Nombre de groupes = trous par position de platine (9 pour 3 × 3) ; vérifiez les nuages sur le graphe des beam shifts.
- Motif de nom : une expression régulière avec un groupe entre parenthèses, p. ex. GridSquare_(\d+).
- Faites-le tôt (sur les films ou micrographies) : les particules extraites héritent des groupes.

**Étapes suivantes** : CTF refinement (RELION), Motion correction (RELION), Particle extraction (RELION)

### Particle sets (intersection, difference)

`relion_particle_sets` — intégré (aucun logiciel externe)

Compare deux jeux de particules (comme le « Particle Sets Tool » de CryoSPARC) : les particules présentes dans les deux, celles de A seulement et celles de B seulement. Les particules se reconnaissent à leur image ou à leur position sur la micrographie. Deux jeux de micrographies se comparent par leur nom, comme avec l'« Exposure Sets Tool ».

**Quand l'utiliser**

- Retirer d'un jeu les particules d'un autre (classes « poubelle », particules déjà traitées).
- Garder les particules retenues par deux nettoyages indépendants (2D et 3D, deux pickings).

**À éviter / pièges**

- La correspondance par image entre particules ré-extraites : elles ont d'autres noms d'image ; prenez la position.

**Entrées conseillées** : Deux jeux de particules (RELION ou CryoSPARC), ou deux jeux de micrographies.

**Conseils**

- Position : distance d'environ un quart du diamètre ; les décalages de l'alignement sont pris en compte.
- Micrographies : par nom complet, ou par nom de fichier quand les dossiers diffèrent (mouvement et CTF).
- L'union des deux jeux se fait avec « Join star files ».

**Étapes suivantes** : 3D auto-refine (RELION), 2D classification (RELION), Join star files (RELION)

### Rebalance orientations

`relion_rebalance` — intégré (aucun logiciel externe)

Retire des particules dans les vues trop représentées (orientation préférentielle), comme « Rebalance Orientations » de CryoSPARC : les directions de vue sont découpées en cases d'aire à peu près égale et chaque case garde au plus le nombre de particules d'un percentile des cases.

**Quand l'utiliser**

- Carte étirée dans une direction, FSC directionnelle très anisotrope, quelques vues qui dominent la carte des directions de vue.

**À éviter / pièges**

- Un jeu déjà petit : retirer des particules coûte de la résolution ; mieux vaut collecter inclinée.

**Entrées conseillées** : Des particules raffinées (avec leurs poses).

**Conseils**

- Cases de 10° et percentile 80 % pour commencer ; comparez les cartes des directions de vue avant et après.
- Raffinez ensuite les particules gardées et comparez l'anisotropie (FSC directionnelle).

**Étapes suivantes** : 3D auto-refine (RELION)

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

### Restack particles

`relion_restack` — logiciel : `relion`

Réécrit les images d'un jeu de particules dans de nouvelles piles (relion_stack_create), comme « Restack Particles » de CryoSPARC : un sous-ensemble gardé après nettoyage ne lit plus que ses propres images, dans des fichiers moins nombreux et plus petits. Les particules peuvent être recentrées sur les décalages de l'alignement, arrondis au pixel (sans interpolation).

**Quand l'utiliser**

- Après le nettoyage 2D/3D d'un grand jeu : les jobs suivants lisent moins de données (disques réseau, cache).
- Avant de supprimer une extraction volumineuse dont on ne garde qu'une partie.

**À éviter / pièges**

- Un jeu encore appelé à changer beaucoup : chaque restack réécrit toutes les images gardées.

**Entrées conseillées** : Des particules (le recentrage demande leurs décalages : classification ou raffinement).

**Conseils**

- Une pile par micrographie par défaut, comme l'extraction ; une pile unique s'écrit image par image.
- Le recentrage arrondi garde le reste du décalage dans le fichier ; pour un vrai recentrage avec nouvelle boîte, ré-extrayez (extraction avec « Particles to re-extract »).

**Étapes suivantes** : 3D auto-refine (RELION), 2D classification (RELION), 3D classification (RELION)

### Subset selection (RELION)

`relion_subset` — logiciel : `relion`

relion_star_handler sur un jeu de particules ou de micrographies : retirer les particules en double (plus proches qu'une distance après alignement : elles gonflent la FSC), découper en sous-ensembles, garder une plage d'une étiquette de métadonnées ou écarter les images aux statistiques aberrantes.

**Quand l'utiliser**

- Après une ré-extraction recentrée : doublons d'un picking trop dense.
- Découper un gros jeu pour des essais rapides.

**À éviter / pièges**

- Distance de doublons proche du diamètre : de vraies particules voisines seraient retirées.

**Entrées conseillées** : Des particules ou des micrographies.

**Conseils**

- Doublons : une distance de 30 à 50 % du diamètre.
- Sélection par valeur : n'importe quelle étiquette (rlnCtfMaxResolution, rlnDefocusU...).

**Étapes suivantes** : 3D auto-refine (RELION), 2D classification (RELION), Join star files (RELION)

### Symmetry expansion (RELION)

`relion_symmetry_expand` — logiciel : `relion`

Copie chaque particule une fois par pose équivalente par symétrie (relion_particle_symmetry_expand), comme la « Symmetry Expansion » de CryoSPARC : chaque copie d'un complexe symétrique devient une particule. Expansion hélicoïdale le long de l'axe aussi.

**Quand l'utiliser**

- Classification ou raffinement focalisés d'une sous-unité d'un complexe symétrique, en C1.
- Sous-unités qui diffèrent d'une copie à l'autre (occupation partielle d'un ligand).

**À éviter / pièges**

- Raffiner les copies avec la symétrie d'origine : les particules seraient comptées plusieurs fois.

**Entrées conseillées** : Des particules raffinées avec la symétrie à développer.

**Conseils**

- Ensuite : soustraction du signal autour d'une sous-unité, puis classification 3D sans alignement ou raffinement local en C1.
- Le nombre de particules est multiplié par l'ordre du groupe (14 pour D7).

**Étapes suivantes** : RELION signal subtraction, 3D classification (RELION), RELION local refinement
