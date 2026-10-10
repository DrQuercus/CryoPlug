# Guide CryoPlug — de la carte CryoSPARC au dépôt PDB/EMDB

Ce guide accompagne un projet complet : évaluer et améliorer la carte, construire le modèle, le corriger,
l'affiner, le valider et préparer le dépôt. Il explique **quel job utiliser, dans quel cas, et comment
lire les résultats**. La référence détaillée de chaque job est dans l'onglet « Référence des jobs » de
la page d'aide (et dans `docs/JOBS.md`).

> CryoPlug orchestre des logiciels reconnus (ModelAngelo, CryoAtom2, LocScale, Phenix, ISOLDE…). Il ne
> remplace pas l'expertise : chaque modèle doit être inspecté dans la carte avant d'être déposé.

## Sommaire

1. [Démarrer](#1-démarrer)
2. [Quelle carte pour quoi ?](#2-quelle-carte-pour-quoi-)
3. [Étape 1 — Évaluer et améliorer la carte](#3-étape-1--évaluer-et-améliorer-la-carte)
4. [Hétérogénéité avec cryoDRGN](#4-hétérogénéité-avec-cryodrgn)
5. [Étape 2 — Construire le modèle](#5-étape-2--construire-le-modèle)
6. [Étape 3 — Corriger et affiner](#6-étape-3--corriger-et-affiner)
7. [Étape 4 — Valider](#7-étape-4--valider)
8. [Étape 5 — Déposer](#8-étape-5--déposer)
9. [Recettes par situation](#9-recettes-par-situation)
10. [Dépannage](#10-dépannage)
11. [Bonnes pratiques](#11-bonnes-pratiques)
12. [Glossaire](#12-glossaire)

---

## 1. Démarrer

### Les concepts

| Concept | Ce que c'est |
|---|---|
| **Projet** | Un dossier `CP-<titre>` sur le serveur. Un projet par jeu de données / structure. |
| **Job** | Une étape (J1, J2…) qui écrit dans son sous-dossier `J<n>` : paramètres, log, fichiers, rapport. |
| **Entrées / sorties** | Un job consomme les sorties d'autres jobs : demi-cartes, carte, masque, modèle, séquence, FSC, rapport, restreintes. |
| **Lane** | Où tourne le job : la machine locale (avec ses GPU) ou une file de cluster (SLURM). La lane par défaut est présélectionnée ; la section *Compute* du constructeur permet d'en choisir une autre et de choisir les GPU. |
| **Résolution « auto »** | La résolution mesurée à l'import est propagée : laissez « auto » dans les jobs suivants. |

Statuts d'un job :

| Statut | Signification |
|---|---|
| building | En préparation : paramètres et entrées modifiables. |
| queued | En file : attend ses jobs parents, un slot de lane ou un GPU (le message dit lequel). |
| launched / running | En cours ; le log se met à jour en direct. |
| waiting | Session interactive (ISOLDE, Coot) prête : à vous de jouer. |
| completed | Terminé ; les sorties sont utilisables. |
| failed / killed | Échec ou arrêt ; lisez le log, corrigez, puis « Clear » pour relancer. |

### Votre premier projet

1. **Projects → New project** : donnez un titre ; le dossier du projet est créé.
2. **Import from CryoSPARC** : indiquez le dossier du job de raffinement final (ex. `/data/CS-projet/J245`).
   Renseignez la symétrie. La FSC est recalculée et la résolution est affichée sur la carte du job.
3. **Import sequence** : collez le FASTA (une entrée par entité distincte).
4. Sur le job d'import, **Continue with…** propose tous les jobs qui acceptent ses sorties, déjà pré-remplis.
   Pendant que le constructeur (**New job**) est ouvert à droite, cliquer sur un job l'ouvre dans la zone
   principale, comme dans CryoSPARC : glissez ses sorties sur les entrées du constructeur (ou bouton
   « Use as input »), puis « Jobs » pour revenir aux cartes.
5. Ou bien **Workflows** : créez toute la chaîne en une fois (par exemple *De novo model → deposition*),
   cochez/décochez les étapes optionnelles, puis « Create & queue all ».
6. Suivez l'avancement dans les cartes, la vue **Graph** et la page **Resources** (onglet *Queue*).

Pour essayer sans données : `cryoplug demo-data ~/cryoplug_demo`, puis importez
`~/cryoplug_demo/CS-demo/J42` (jeu synthétique).

### Sélection et raccourcis

Le bouton **⋯** d'une carte (ou un **clic droit**) réunit les actions du job : ouvrir, *Continue with…*,
voir en 3D, cloner, réinitialiser, copier le chemin du dossier, supprimer. Le résumé en haut à droite
compte les jobs par statut ; un clic sur un statut (par exemple *failed*) n'affiche que ces jobs.

Comme dans CryoSPARC : **Ctrl/⌘ + clic** ajoute un job à la sélection, **Maj + clic** une plage,
**Ctrl/⌘ + A** tous les jobs affichés ; la barre qui apparaît met en file, arrête, réinitialise ou supprime
les jobs sélectionnés. Quand un job est ouvert, ses entrées sont entourées en violet et les jobs qui
l'utilisent en vert. Touches : **N** nouveau job, **/** filtrer, **G** cartes ⇄ graphe, **Échap** fermer,
**?** liste des raccourcis.

### Comptes utilisateurs et réglages

Comme dans CryoSPARC, chacun peut avoir son compte. Dès qu'un compte existe, tout le monde se connecte avec
son nom d'utilisateur et son mot de passe. L'onglet **Settings** réunit tout :

| Onglet | Pour qui | Contenu |
|---|---|---|
| **My account** | chacun | Nom, e-mail, changement de mot de passe, navigateurs connectés (« déconnecter les autres »), vos dossiers. |
| **Users** | administrateurs | Ajouter, modifier, désactiver, supprimer un compte ; lui donner un nouveau mot de passe (généré, affiché une fois, à changer à la connexion) ; son **dossier de projets** et les **dossiers qu'il peut lire**. |
| **Compute** | administrateurs | La machine et ses GPU ; les **lanes** (cette machine, cluster SLURM) : GPU distribués, partitions, durée, mémoire, options `#SBATCH`, lignes à exécuter avant le job ; *Test* et aperçu du script de soumission. |
| **Access & security** | administrateurs | Mode de protection, adresse d'écoute, chiffrement, durée des sessions, longueur minimale des mots de passe. |

- Un **utilisateur** voit ses projets et ceux partagés avec lui, crée ses projets dans son dossier et ne
  choisit des fichiers (import CryoSPARC, cartes, modèles, bases de séquences) que dans ses dossiers ; le
  navigateur de fichiers propose un raccourci vers chacun. Un **administrateur** voit et lit tout.
- **Partager un projet** : menu **⋯** de la carte du projet › *Share…* (propriétaire ou administrateur). Les
  membres travaillent dans le projet ; les données qu'ils ajoutent doivent être dans leurs propres dossiers.
- Réservé aux administrateurs, car capable d'exécuter du code ou de lire n'importe où : le job *Custom
  command*, les commandes ChimeraX supplémentaires d'ISOLDE et les *Extra arguments* des programmes.
- Premier compte : **Settings › Users › Create the administrator account** (les projets existants deviennent
  les siens), ou `cryoplug user add admin --admin` sur le serveur. Mot de passe administrateur oublié :
  `cryoplug user passwd <nom>`.
- Les jobs tournent sous le compte Unix qui a lancé CryoPlug (comme le compte `cryosparc`) : pour une
  séparation stricte des données, utilisez aussi les permissions Unix.

### GPU, lanes et suivi des ressources

Comme dans CryoSPARC, un job part sans rien régler sur la lane par défaut ; la section **Compute** du
constructeur permet de décider autrement :

- **Lane** : la machine locale ou une lane SLURM (la liste indique ce que chacune offre).
- **GPU** : *Automatic* prend, au démarrage du job, le GPU libre dont la mémoire est la moins utilisée ;
  *Choose GPUs* montre chaque GPU en direct (mémoire, charge, température, job qui l'occupe). Choisir un GPU
  occupé est permis : le job attend qu'il se libère.
- **CPU threads**, et pour SLURM la **partition**, la **durée** et la **mémoire** du job (vides : réglages
  de la lane).

La page **Resources** donne la vue d'ensemble du matériel : **Live usage** (CPU, mémoire, chaque GPU et ses
processus, disques et espace libre, réseau, processus façon htop, graphes des 5 dernières minutes),
**Queue** (lanes, GPU occupés, jobs actifs) et **Cluster** (partitions SLURM, nœuds, jobs en attente).
Les administrateurs règlent les lanes dans **Settings › Compute**.

### Ouvrir CryoPlug depuis votre portable

Par défaut, l'interface ne s'ouvre que sur le serveur lui-même (`http://localhost:39500`). Pour l'utiliser
depuis un autre ordinateur, mettez `host = "0.0.0.0"` dans la section `[server]` de la configuration et
redémarrez CryoPlug. Avec des comptes, chacun ouvre `http://<serveur>:39500` et se connecte ; sans compte, le
terminal affiche des liens `http://<serveur>:39500/?token=…` qui vous connectent directement (la commande
`cryoplug url` les réaffiche), le temps de créer le compte administrateur. Hors du labo, passez par un tunnel
SSH (`ssh -N -L 39500:localhost:39500 utilisateur@serveur`, puis `http://localhost:39500` sur le portable).
Le port ouvert, préférez HTTPS. Détails, pare-feu et HTTPS : **Help → Installation & commandes**.

---

## 2. Quelle carte pour quoi ?

C'est la source d'erreurs la plus fréquente : chaque logiciel attend un type de carte précis.

| Carte | Utiliser pour | Ne pas utiliser pour |
|---|---|---|
| **Demi-cartes non filtrées** (`half_maps`) | FSC, résolution directionnelle, LocScale, spIsoNet, Servalcat, modification de densité Phenix, dépôt EMDB (exigées) | Visualiser, construire |
| **Carte non affûtée** (`map`) | Auto-sharpen Phenix, LocScale/EMmerNet sans demi-cartes | Construction automatique |
| **Carte affûtée** (`map_sharp`) | Construction, ajustement, ISOLDE/Coot, affinement, validation ; **carte primaire déposée** | — |
| **Carte LocScale / modification de densité** | Interprétation, construction, ISOLDE | Carte primaire du dépôt (possible en carte additionnelle) |
| **Cartes « réseau »** (DeepEMhancer, EMmerNet, EMReady) | Visualisation, aide à l'interprétation, figures | Validation, construction automatique (DeepEMhancer), carte primaire |
| **Demi-cartes spIsoNet** | Interprétation | FSC rapportée, Servalcat (plus indépendantes) |
| **Masque FSC** | FSC, 3D FSC, LocScale, spIsoNet ; dépôt (recommandé) | — |

**Règle d'or : construisez avec la carte la plus lisible, mais validez et déposez contre la carte primaire.**

---

## 3. Étape 1 — Évaluer et améliorer la carte

### 3.1 Vérifier la résolution et l'anisotropie

- **Half-map FSC** (`map_fsc`) recalcule la FSC gold-standard. On rapporte la courbe **corrigée**
  (randomisation de phase) au seuil 0,143. Un écart important avec CryoSPARC indique en général un masque différent.
- **Directional resolution** (`directional_fsc`) mesure la résolution dans chaque direction.
  Lisez le rapport *pire / meilleure direction* :

| Rapport | Interprétation | Action |
|---|---|---|
| ≤ 1,25 | Carte isotrope | Rien de particulier |
| 1,25 – 1,6 | Anisotropie modérée | LocScale ; surveillez l'aspect étiré de la densité |
| > 1,6 | Anisotropie forte (orientation préférentielle) | spIsoNet, et si possible nouvelle collecte inclinée ou rééquilibrage des vues |

### 3.2 Choisir une méthode d'amélioration

| Situation | Recommandé | Alternatives / remarques |
|---|---|---|
| Résolution homogène, affûtage CryoSPARC satisfaisant | Garder `map_sharp` | Auto-sharpen Phenix pour comparer |
| Cœur net, périphérie / domaines / micelle flous | **LocScale 2** model-free | Puis model-based ou hybride avec le premier modèle |
| 3–5 Å, interprétation difficile | **Modification de densité Phenix** (avec séquence) | LocScale |
| Orientation préférentielle forte | **spIsoNet** | Déposer aussi les cartes d'origine |
| 4–6 Å, figures ou ajustement de domaines | DeepEMhancer, EMReady | Jamais comme seule preuve |
| Doute sur un détail (ligand, chaîne latérale) | Comparer 2–3 cartes | Workflow « Map enhancement comparison » |

Le workflow **Map enhancement comparison** lance ces méthodes côte à côte sur les mêmes demi-cartes ;
comparez-les dans le visualiseur 3D (bouton *View 3D*) avant de choisir.

### 3.3 Vérifier la main

Une main inversée donne des hélices gauches et des constructions automatiques en petits fragments.
Corrigez avec **Map operations → Flip handedness** sur la carte utilisée pour la construction
(et retournez de la même façon les autres cartes si vous changez la carte déposée).

### 3.4 Résolution locale

- **Local resolution** (`local_resolution`, Phenix) calcule la résolution de chaque région à partir des deux
  demi-cartes brutes et indépendantes. Le rapport donne la distribution dans la molécule (dans le masque, autour du
  modèle ou dans la densité) et, avec un modèle, la **résolution de chaque chaîne** (tableau trié) et de chaque
  résidu (fichier CSV, et `model_local_resolution.cif` avec la résolution dans la colonne B-factor pour ChimeraX,
  Coot ou PyMOL).
- Dans le visualiseur 3D : menu **Colour** de la carte → *Local resolution* (bleu = meilleure, rouge = moins
  bonne ; échelle réglable, par défaut du 5e au 95e centile), ou `color #1 localres 3 6`. Le bouton *View 3D* du
  job ouvre directement la carte coloriée. Une carte de résolution locale importée de CryoSPARC s'utilise de la
  même façon.
- Elle sert à choisir les régions à raffiner localement, à décider où construire les chaînes latérales, et à faire
  la figure de résolution locale de l'article.

### 3.5 Masques

**Create mask** (`create_mask`, intégré) fabrique des masques à bord doux sur la grille d'une carte :

| Source | Usage typique | Réglages de départ |
|---|---|---|
| Densité de la carte (passe-bas + seuil) | Masque FSC, LocScale, spIsoNet, dépôt | Passe-bas 15 Å, dilatation 3 Å, bord 6 Å |
| Atomes du modèle (chaînes ou résidus choisis) | Masque serré autour d'un modèle | Rayon 3 Å, dilatation 2–3 Å, bord 6 Å |
| Densité proche du modèle | **Raffinement local** d'une sous-partie : suit la densité, y compris non modélisée | Distance 6–8 Å, dilatation 3–5 Å, bord 8–12 Å |

- **Seuil automatique** : le niveau qui contient le volume de la masse attendue (champ *Expected mass*, sinon la
  masse des atomes choisis), à défaut la méthode d'Otsu. Le rapport montre l'histogramme, le seuil en σ, le volume
  et la masse équivalente, et trois coupes avec le masque en orange.
- **Sélection** : `A, B, C:10-250` ; *Leave out* retire des chaînes ou des segments ; *Remove blobs lighter than*
  élimine les petits îlots de bruit.
- **Combine with mask** : union, soustraction (masque du complexe moins la région à garder, pour une soustraction
  de signal) ou intersection.
- Le rapport donne le chemin du fichier pour l'importer dans CryoSPARC (*Import 3D Volumes*, type *mask*) : même
  boîte et même taille de pixel que la carte d'origine.

### 3.6 Gros complexes : cartes composites

Un gros complexe est souvent résolu par une carte consensus et plusieurs **raffinements locaux** dans CryoSPARC.
Importez chacun (un *Import from CryoSPARC* par raffinement), ajustez le modèle dans la carte consensus, puis
lancez **Composite map** (`composite_map`, phenix.combine_focused_maps) :

1. *Reference map* = la carte consensus ; *Focused map 1, 2…* = les cartes locales (un nouvel emplacement apparaît
   dès que le précédent est rempli, jusqu'à 8).
2. Les demi-cartes de chaque import sont retrouvées automatiquement et combinées de la même façon : on obtient des
   **demi-cartes composites**, pour la FSC et la résolution locale de la carte composite.
3. Construisez et affinez dans la carte composite ; déposez-la avec la carte consensus et les cartes locales (et
   leurs demi-cartes). Ne présentez pas la FSC de la carte composite comme une FSC gold-standard.

### 3.7 Hétérogénéité : variabilité 3D et cryoDRGN

Une région floue dans la carte consensus (résolution locale médiocre, densité faible) peut venir d'un **mouvement**
(un domaine qui bouge) ou d'une **occupation partielle** (une sous-unité absente d'une partie des particules), ou d'un
mélange d'états. Deux approches, qui se complètent :

| | CryoSPARC 3D Variability (3DVA) | cryoDRGN |
|---|---|---|
| Principe | Composantes **linéaires** de variabilité autour de la carte consensus | Réseau de neurones : espace latent **non linéaire**, états discrets et continus |
| Dans CryoPlug | *Import from CryoSPARC* du job 3DVA (coordonnées des particules) et *Import volume series* du job *3D Variability Display* | *Import from CryoSPARC* du raffinement consensus, puis *cryoDRGN training* (GPU) |
| Coût | Déjà calculé dans CryoSPARC | Une à quelques heures de GPU selon le nombre de particules et la taille d'image |

**cryoDRGN a son propre chapitre** (§4) : protocole en deux tours, contrôle des entrées, lecture des rapports,
explorateur, nettoyage du junk, convergence, paysage des états et validation.

**3D Variability (CryoSPARC), pas à pas**

1. **Import from CryoSPARC** du job 3DVA : les coordonnées de chaque particule le long des composantes sont
   importées comme un espace latent (explorateur, clusters k-means) ; **Import volume series** du job *3D Variability
   Display* (dossier ou ZIP) donne une série de volumes par composante. Le workflow *3D variability (CryoSPARC) →
   interpretation* crée cette chaîne en un clic.
2. **Explorateur** : comme pour cryoDRGN (§4.6) — clusters, *Keep… / Remove…*, lasso, *Outliers*, images des particules.
3. **Volume series analysis** sur une composante (ou sur des volumes cryoDRGN : clusters, états, trajectoire), avec le
   modèle ajusté dans la carte consensus :
   - la **carte de variabilité** (écart-type entre volumes, en σ de la densité de la molécule) ; *View 3D* l'affiche
     en couleur sur la carte moyenne (bleu = stable, rouge = variable), et le menu *Colour* l'applique à n'importe
     quelle carte ou série ouverte ;
   - la **matrice de corrélation** entre volumes : des **blocs** = des états distincts, un **dégradé** le long de la
     diagonale = un mouvement continu ;
   - avec un modèle, la **densité de chaque chaîne dans chaque volume** (1 = aussi dense que le reste du modèle,
     0 = absente) et les chaînes les plus variables : une chaîne marquée *fades in some frames* est absente d'une
     partie des particules ou se déplace hors de sa place dans le modèle — la série jouée dans le visualiseur tranche.
4. **Extract volume from series** (ou *Extract map…* dans l'explorateur) : la carte d'un état pour y ajuster le modèle.

**Lire une série dans le visualiseur 3D.** Le bouton *Play in 3D* d'une sortie *Volume series* charge tous les volumes
(aperçus de 128 voxels de côté au plus) puis les joue : barre de lecture sur la vue (lecture/pause, volume précédent
/ suivant, curseur, vitesse, boucle ou aller-retour), touches **Espace**, **,** et **.** ; commandes `play 10`,
`stop`, `frame 5`, `frame next`, ou à la ChimeraX `vseries play #1 direction oscillate`. Le seuil est le même pour tous
les volumes de la série, et la coloration par variabilité ou résolution locale s'applique à chacun.

> ⚠️ Les volumes cryoDRGN et les frames de 3DVA sont **générés** (par le réseau, ou par une combinaison linéaire) :
> ce ne sont pas des reconstructions gold-standard. Ne déposez pas leur résolution et confirmez tout état important
> par une reconstruction des particules sélectionnées (demi-cartes indépendantes) dans CryoSPARC.

---

## 4. Hétérogénéité avec cryoDRGN

cryoDRGN (Zhong et al. 2021) apprend, à partir des images de particules et des poses du raffinement consensus, un
**espace latent** : chaque particule y reçoit des coordonnées, et deux particules proches ont des structures proches.
Un réseau de neurones (le *décodeur*) génère le volume correspondant à n'importe quel point de cet espace. On y voit
des **états discrets** (îlots), des **mouvements continus** (bandes), des **sous-unités absentes** d'une partie des
particules, et surtout le **junk** qu'on n'avait pas su retirer.

CryoPlug suit le protocole publié par les auteurs (Kinman et al. 2023, *Nature Protocols* 18, 319) et ajoute à chaque
étape les contrôles et les explications qui permettent de savoir si le résultat est fiable.

### 4.1 Le principe en bref

- **Encodeur** : lit une image (et sa pose, son CTF) et la place dans l'espace latent (*z*, 8 dimensions par défaut).
- **Décodeur** : à partir d'un point *z*, produit un volume 3D. L'entraînement ajuste les deux réseaux pour que les
  projections des volumes générés ressemblent aux images.
- **Époque** : un passage complet sur toutes les particules. Le protocole en fait 50, puis vérifie la convergence.
- **UMAP / PCA** : projections en 2D de l'espace latent pour l'explorer. La PCA est linéaire (ses axes ont un sens) ;
  l'UMAP sépare mieux les îlots mais **ses distances entre îlots ne se lisent pas**.
- **Volumes « on-data »** : CryoPlug et cryoDRGN génèrent les volumes aux coordonnées de **vraies particules** (le
  centre d'un cluster k-means est remplacé par la particule la plus proche), jamais dans des régions vides.

> ⚠️ Les volumes cryoDRGN sont **générés** par un réseau : ce ne sont pas des reconstructions gold-standard. Ils
> montrent les états ; une carte publiable d'un état vient d'une reconstruction de ses particules (§4.10).

### 4.2 Le protocole en un coup d'œil

| Étape | Job CryoPlug | Réglages | Ce qu'on vérifie |
|---|---|---|---|
| 1. Importer | *Import from CryoSPARC* (raffinement consensus) | — | Particules avec poses (`alignments3D`) et CTF |
| 2. Contrôler les entrées | *cryoDRGN input check* (ou automatique dans l'entraînement) | 128 px, 10 000 images | Carte rétroprojetée semblable au consensus, densité positive |
| 3. Tour 1 : nettoyage | *cryoDRGN training* | 128 px, petit réseau 256 × 3, z = 8, 50 époques | Courbes de perte, junk (galerie, outliers) |
| 4. Retirer le junk | Explorateur → *Remove…* → *Select particles* | — | Classification 2D des particules écartées dans CryoSPARC |
| 5. Tour 2 : états | *cryoDRGN training* sur la sélection | 256 px, grand réseau 1024 × 3 (auto), 50 époques | États, mouvements |
| 6. Convergence | *cryoDRGN convergence check* → *continue training* si besoin | 100 époques au besoin | Volumes stables d'une époque à l'autre |
| 7. Analyser | Explorateur, *analysis*, *trajectory*, *landscape*, *volumes at particles* | k = 50–100 pour les états rares | États, chemins soutenus par les données |
| 8. Valider | *Keep…* → CryoSPARC (Import Particle Stack → NU refine) ; *Volume series analysis* | — | Une carte gold-standard par état |

Le workflow *Conformational heterogeneity (cryoDRGN)* crée les étapes 1 à 3 et le contrôle de convergence en un clic.

### 4.3 Préparer et contrôler les entrées

**Quelles particules ?** Celles du **raffinement consensus** (NU-refine ou homogeneous) : leurs poses servent telles
quelles. Évitez les classifications 2D/3D agressives avant cryoDRGN : elles retirent justement l'hétérogénéité qu'on
cherche (le nettoyage se fait dans cryoDRGN, étape 4). Pour un raffinement hétérogène ou un ab initio, réglez
*Poses from* (les décalages et les poses ne se lisent pas de la même façon).

**Taille d'image.** Les images sont réduites par recadrage de Fourier : la résolution atteignable est limitée à deux
fois la nouvelle taille de pixel (Nyquist). Exemple : particules de 400 px à 0,83 Å → 128 px à 2,6 Å/px (Nyquist
5,2 Å, assez pour voir les états) ; 256 px à 1,3 Å/px pour le tour final. Les images réduites sont gardées en sortie
(*particles_prepared*) et réutilisées par les jobs suivants à la même taille, y compris sur une sélection.

**Le contrôle des entrées** (job *cryoDRGN input check*, ou paramètre *Input check* de l'entraînement) rétroprojette
les 10 000 premières particules avec leurs poses et leur CTF. Le rapport donne :

- la **FSC entre deux demi-lots** dans une sphère douce : elle doit montrer du signal bien au-delà des premières
  coquilles (cible indiquée dans le tableau) ;
- le **signe de la densité** : la molécule doit être **positive** (plus dense que le solvant) ;
- un **verdict** et, si besoin, quoi faire ; la carte *backprojection* s'ouvre en 3D à côté de la carte consensus
  (même forme, même main).

Une densité négative veut dire que le contraste des images est l'inverse de ce qu'attend cryoDRGN : cochez *Do not
invert the images*, sinon tous les volumes sortent creux. Une FSC qui s'effondre tout de suite veut dire des poses mal
lues. Avec *check, stop if it fails*, l'entraînement ne démarre pas sur des entrées fausses (des heures de GPU
économisées).

### 4.4 Entraîner : les paramètres

| Paramètre | Tour 1 (nettoyage) | Tour 2 (final) | Quand le changer |
|---|---|---|---|
| *Image size* | 128 px | 256 px | Plus petit pour une très grosse particule ou beaucoup de particules |
| *Latent dimensions* (z) | 8 | 8 | 1–2 seulement pour un mouvement unique et simple ; au-delà de 10, rarement utile |
| *Epochs* | 50 | 50 (→ 100 si non convergé) | Voir §4.8 |
| *Network size* | auto → petit (256 × 3) | auto → grand (1024 × 3) | Le mode auto suit le protocole (grand réseau au-delà de 128 px ou sur une sélection) |
| *Input check* | check, then train | no check (déjà fait) | *stop if it fails* pour un long job sans surveillance |
| *Volumes sampled* | 20 | 20 | 50–100 (dans *cryoDRGN analysis*) pour chercher des états rares |
| *GPUs* | 1 | 1–4 | Plusieurs GPU (`--multigpu`) surtout utiles à 256 px avec le grand réseau |
| *Lazy loading* | auto | auto | Activé tout seul si les images ne tiennent pas en mémoire (disque rapide conseillé) |
| *Batch size* | 16 | 16 | Plus bas si le GPU manque de mémoire |
| *β (KL)* | 1/z | 1/z | Plus grand = espace latent plus lisse et moins détaillé |
| *Mixed precision* | oui | oui | À décocher si l'entraînement s'arrête sur une erreur d'assertion |
| *Refine the poses* | non | non | Poses de qualité moyenne (le protocole garde les poses consensus) |
| *Checkpoint every* | 1 | 1 | Plus grand pour économiser le disque (le contrôle de convergence compare les points de sauvegarde) |

Pendant l'entraînement, la carte du job affiche l'époque, le temps par époque et le **temps restant** ; les courbes de
perte se dessinent dans le rapport à chaque époque. La version de cryoDRGN est lue au démarrage (les options et la
numérotation des époques changent entre les versions 3.x et 4.x) ; en cas d'échec, le message propose la cause la
plus probable (mémoire GPU, mémoire vive, précision mixte).

### 4.5 Lire le rapport d'un entraînement

- **Tuiles du haut** : contrôle des entrées, temps par époque, nombre de particules, dimension latente et nombre de
  clusters, clusters signalés.
- **Training loss** : l'erreur de reconstruction baisse vite puis se stabilise. **KL divergence** : monte pendant les
  premières époques puis se stabilise.
- **Explorateur** (§4.6) : le cœur du rapport.
- **Cluster volumes** (galerie sous l'explorateur) : trois projections de chaque volume, à la même échelle de gris.
  Signaux d'alerte : **weak** (peu de densité au seuil commun : morceaux manquants ou junk), **noisy** (densité
  éparpillée hors de la molécule), **blurry** (aucun détail fin : agrégats, junk), **rare** (moins de 1 % des
  particules). Ce sont des indices : regardez toujours le volume en 3D.
- **Is the latent space structural?** : la part du **défocus**, de la **direction de vue** et du **décalage** que
  l'espace latent permet de prédire (R²). Proche de 0 = l'espace latent reflète la structure, comme il se doit.
  Au-delà de 0,3, une partie de l'hétérogénéité vient des images (CTF, glace, vues) et non des molécules : coloriez
  l'explorateur par cette propriété pour voir où.
- **Variance along the principal components** : une composante dominante = un mouvement ou un changement de
  composition principal ; une variance étalée = plusieurs changements indépendants (ou du bruit).
- **Viewing directions** : couverture des orientations ; une bande vide = des vues manquantes.
- **Latent clusters** et **Similarity of the cluster volumes** : des blocs dans la matrice = des états distincts ; un
  dégradé le long de la diagonale = un mouvement continu ; un volume isolé = un état rare ou du junk.
- **What to do next** : la marche à suivre, rédigée d'après ce que l'analyse a trouvé (clusters suspects, outliers,
  propriétés codées, tour 1 ou tour 2).

### 4.6 Utiliser l'explorateur

Chaque point est une particule (12 000 au plus sont affichées ; les sélections portent toujours sur **toutes** les
particules). Les numéros sont les centres des clusters k-means.

- **Choisir des clusters** : clic sur un centre, une pastille ou une vignette de la galerie ; **double-clic** = ouvrir
  son volume en 3D. Puis :
  - *View / Play in 3D* : les volumes dans le visualiseur, joués dans l'ordre des clics, au même seuil ;
  - *Keep… / Remove…* : prépare *Select particles* (garder un état, retirer du junk) ;
  - *Trajectory…* : prépare *cryoDRGN trajectory* à travers ces clusters, dans l'ordre des clics ;
  - *Extract map…* : la carte d'un cluster pour ajuster un modèle ;
  - *Particle images* : 24 particules du cluster tirées au hasard (filtrées passe-bas pour les rendre visibles ;
    *Others* en tire d'autres) — le junk se reconnaît souvent sur les images.
- **Colour by** : *Density* (régions peuplées), *‖z‖* (distance au centre : les particules isolées, souvent du
  junk), *PC1–3*, *Defocus*, *Viewing tilt / azimuth*, *In-plane shift*. Une barre de couleurs et une note expliquent
  chaque coloration. Des clusters qui suivent le défocus ou l'orientation sont rarement de vrais états.
- **Lasso** (ou glisser en tenant **Maj**) : entourez une région de particules, puis *Keep region…*, *Remove region…*,
  *Volume of the region…* (le volume au centre de la région, ou plusieurs répartis dedans) ou *Particle images*.
- **Une particule** : un clic la marque ; *Volume of particle …* prépare le job qui génère son volume.
- **Outliers** : montre en rouge les particules au-delà de ‖z‖ = moyenne + 2 écarts-types ; *Remove outliers…*
  prépare leur retrait.
- **UMAP / PCA** : basculer de projection ; le contrôle de convergence ajoute une projection par époque et le bouton
  *Play the epochs*.

### 4.7 Nettoyer les particules

Le premier tour sert surtout à trouver le junk. Il se reconnaît à :

- des volumes cassés, en blob, bruités ou sans détail (signalés dans la galerie) ;
- des particules isolées loin des autres (outliers, ‖z‖ élevé) ;
- des îlots qui suivent le défocus ou une orientation (tableau *Is the latent space structural?*) ;
- des images de particules sans forme reconnaissable (*Particle images*).

Retirez-les (*Remove…*, *Remove region…*, *Remove outliers…*), puis vérifiez dans CryoSPARC que les **particules
écartées** (sortie *excluded*) sont bien du junk : une classification 2D rapide suffit. Ne nettoyez pas trop : un petit
cluster au volume net est peut-être l'état rare que vous cherchez. Le tour 2 se lance sur la sélection (*cryoDRGN
training* sur la sortie de *Select particles*) : les images déjà réduites sont réutilisées et le grand réseau est
choisi automatiquement. Un second nettoyage est possible si le tour 2 montre encore du junk.

### 4.8 Vérifier la convergence

Un entraînement trop court mélange les états ; trop long, il apprend le bruit des images (volumes de plus en plus
bruités : sur-apprentissage). Le job *cryoDRGN convergence check* compare les points de sauvegarde de l'entraînement :

- la **courbe de perte** ;
- le **déplacement des particules** dans l'espace latent d'une époque à l'autre (il baisse puis plafonne au niveau du
  bruit de l'optimisation) ;
- la **stabilité des voisinages** (part des 15 voisins de chaque particule conservée d'une époque à l'autre) ;
- les **volumes de particules représentatives** générés à plusieurs époques et leur corrélation (planche *Representative
  particles* et sorties *Particle … across epochs* à jouer en 3D).

Le **verdict** est en tête du rapport : *converged* (les résultats de la dernière époque sont utilisables),
*nearly converged* ou *not converged* (prolongez avec *cryoDRGN continue training*, par exemple de 50 à 100 époques,
puis refaites le contrôle). Si les volumes deviennent plus bruités aux dernières époques, analysez l'époque indiquée
(*cryoDRGN analysis* avec *Epoch*). Une perte qui baisse encore un peu n'empêche pas la convergence : ce sont les volumes
et l'organisation de l'espace latent qui décident.

### 4.9 Explorer les états et les mouvements

- **cryoDRGN analysis** : plus de volumes (50–100) pour voir des états rares, une autre époque, la main inversée
  (*Flip handedness*) si la consensus avait la mauvaise main.
- **Trajectoires** (*cryoDRGN trajectory*) : *through the particles* suit le graphe des plus proches voisins et reste
  dans les régions peuplées (états que les données contiennent) ; *straight line* peut traverser des régions vides.
  *Back to the start* ferme la boucle ; on peut relier deux particules précises plutôt que deux clusters. Les
  trajectoires le long des composantes principales (PC1, PC2) montrent les axes de variation, **pas** forcément un
  chemin biologique.
- **Volumes at particles** : le volume d'une particule ou d'une région dessinée au lasso.
- **Landscape analysis** (*cryodrgn analyze_landscape*) : 1000 volumes générés dans tout l'espace latent sont comparés
  par ACP (dans un masque, facultatif) et regroupés en **états** (10 par défaut). Des particules éloignées dans l'UMAP
  mais au même volume se retrouvent ensemble. Résultats : les volumes des états, les trajectoires le long des
  composantes des volumes, et un espace latent où les états remplacent les clusters (explorateur, *Keep…*).
- **Volume series analysis** sur les volumes des clusters, des états ou d'une trajectoire, avec le modèle : carte de
  variabilité et chaînes qui bougent ou disparaissent (§3.7).

### 4.10 Valider et publier

- Pour chaque état d'intérêt : *Keep…* dans l'explorateur, puis dans CryoSPARC *Import Particle Stack* avec le fichier
  `.cs` produit (le rapport donne le chemin et le dossier des images) et *Homogeneous / NU refinement* : demi-cartes
  indépendantes, FSC, résolution à déposer.
- Dans l'article : version de cryoDRGN (dans le log et les paramètres du job), taille d'image, dimension latente,
  réseau, nombre d'époques, époque analysée, preuve de convergence, nombre de particules par état, critères de
  nettoyage.
- Ne déposez pas la « résolution » d'un volume cryoDRGN : elle n'existe pas au sens gold-standard.

### 4.11 Pièges fréquents

| Symptôme | Cause probable | Solution |
|---|---|---|
| Volumes creux ou « à l'envers » | Contraste des images inversé par rapport à ce qu'attend cryoDRGN | Contrôle des entrées (densité négative) → *Do not invert the images* |
| Tous les volumes ressemblent à du junk | Seuil d'affichage trop bas, ou poses / CTF mal lus | Monter le seuil ; contrôle des entrées ; *Poses from* |
| Tous les volumes sont identiques | Particules trop filtrées en amont, ou peu d'hétérogénéité | Repartir de particules moins filtrées ; grand réseau au tour 2 |
| L'espace latent suit le défocus | Erreurs de CTF, épaisseur de glace | Ce n'est pas structural : retirez les particules en cause ou ignorez cet axe |
| Les clusters correspondent à des vues | Erreurs de poses, orientation préférentielle, junk vu sous un angle | Carte des directions de vue ; colorier par *Viewing tilt / azimuth* |
| `CUDA out of memory` | Batch trop gros pour le GPU | *Batch size* 8 ; taille d'image plus petite ; petit réseau |
| Le job est tué, `MemoryError` | Images trop grosses pour la mémoire vive | *Lazy loading* (automatique si détecté) ; demander plus de mémoire au lane |
| `AssertionError` pendant l'entraînement | Instabilité numérique de la précision mixte | Décocher *Mixed precision* |
| « must be greater than … » en continuant | Le nombre d'époques final est plus petit que celui du point de sauvegarde | *Train until epoch* plus grand |
| Volumes de plus en plus bruités | Sur-apprentissage | Analyser une époque antérieure (contrôle de convergence) |
| « Cannot find file … under datadir » | Projet CryoSPARC déplacé | *Import particles* avec *Image folder* |
| Options refusées par cryodrgn | Version ancienne | CryoPlug s'adapte aux versions 3.x et 4.x ; mettez à jour (`pip install -U cryodrgn`) |

### 4.12 Mémo

- **Tour 1** : *cryoDRGN training* sur les particules du raffinement consensus, 128 px, réglages par défaut. Lire :
  contrôle des entrées, galerie, outliers, tableau des propriétés codées. Retirer le junk.
- **Tour 2** : *cryoDRGN training* sur la sélection, 256 px (grand réseau automatique), 50 époques. Puis
  *cryoDRGN convergence check* → *continue training* jusqu'à convergence.
- **Analyse** : explorateur (couleurs, lasso, images), *analysis* avec plus de volumes, *trajectory* à travers les
  particules, *landscape analysis* pour des états, *Volume series analysis* avec le modèle.
- **Validation** : une reconstruction CryoSPARC par état, avant toute interprétation fine.

---

## 5. Étape 2 — Construire le modèle

### 5.1 Choisir la stratégie

| Situation | Stratégie | Jobs |
|---|---|---|
| Résolution ≲ 4 Å, séquence connue | Construction de novo | ModelAngelo build **et/ou** CryoAtom2 build ; gardez le meilleur |
| Complexe protéine–ARN/ADN | De novo avec FASTA séparés | CryoAtom2 build (ou ModelAngelo) |
| Sous-unité inconnue / densité inattendue | Identifier puis reconstruire | ModelAngelo sans séquence → Identify chains → CryoAtom2 avec les séquences trouvées (workflow « Identify unknown proteins ») |
| 4–8 Å ou régions mal résolues | Modèle prédit puis ajustement | AlphaFold DB / ColabFold / Boltz-2 → Process predicted model → Rigid-body fit ou Dock in map → Real-space refinement (morphing) |
| Ligand, cofacteur, médicament | Pose initiale + restreintes | Boltz-2 (pose) + Ligand restraints (eLBOW) → affinement → vérification Fo-Fc (Servalcat) |

### 5.2 Contrôler une construction automatique

Ouvrez le job et vérifiez :

- **Résidus construits** par rapport à la séquence (tableau « Polymer chains ») ; la sortie *raw* montre tout ce qui a été tenté ;
- **CC (mask)** et, mieux, un job **Map-model validation** (Q-score) ;
- la carte et le modèle dans le **visualiseur 3D** : hélices droites, chaînes continues, résidus dans la densité.

Lancer ModelAngelo et CryoAtom2 sur la même carte puis comparer les Q-scores est peu coûteux et souvent instructif.

**Dans le visualiseur 3D** (*View 3D*, ou bouton **3D** au survol d'une carte de job), pour juger
l'ajustement comme dans Coot ou ChimeraX :

1. le modèle s'ouvre avec la carte dans laquelle il a été construit, en maillage (*Mesh*) ;
2. cochez **Zone** (3 Å) : seule la densité autour du modèle reste, le bruit disparaît (`zone 3`) ;
3. réglez le seuil en faisant glisser le trait de l'histogramme, ou avec **+ / −** ; le panneau indique le
   niveau en σ et le pourcentage de voxels au-dessus ;
4. **cliquez sur un résidu** pour recentrer la vue dessus, ou tapez `view /A:45` (« Go to » `A:45` dans le
   panneau du modèle) ; **Slab** limite l'affichage à une tranche autour du centre ;
5. **Slices** affiche des coupes 2D de la carte : pratique pour repérer un masque trop serré, une
   anisotropie ou une boîte décentrée.

`help` dans la ligne de commande liste les commandes (`level`, `style`, `color`, `view`, `zone`, `volume`,
`bg`, `slab`, `save`…), **?** les gestes souris et les touches.

### 5.3 Assembler

Des domaines ou sous-unités dockés séparément se combinent avec **Merge models** (les identifiants de
chaîne en conflit sont renommés). **Model operations** renomme les chaînes comme dans l'article.

---

## 6. Étape 3 — Corriger et affiner

### 6.1 La boucle typique

```
construction auto / docking → Real-space refinement → ISOLDE (ou Coot) → Real-space refinement final → validation
                                        ↑                                                         |
                                        └──────────────── tant que la validation s'améliore ──────┘
```

Les workflows *De novo* et *Predicted model* créent exactement cette boucle ; l'étape ISOLDE met la
chaîne en pause jusqu'à ce que vous ayez fini.

### 6.2 ISOLDE ou Coot ?

| | ISOLDE | Coot |
|---|---|---|
| Principe | Dynamique moléculaire guidée par la carte | Construction manuelle |
| Idéal pour | Corriger un modèle entier (registre, rotamères, boucles, géométrie), 2,5–4,5 Å | Boucles manquantes, ligands, glycanes, eaux, retouches ponctuelles |
| Point fort | Géométrie physiquement réaliste, outliers éliminés interactivement | Contrôle fin, outils de validation intégrés |

### 6.3 Phenix ou Servalcat ?

| | Phenix real_space_refine | Servalcat |
|---|---|---|
| Cible | Une carte (affûtée) | Les demi-cartes non affûtées |
| Atouts | Restreintes de structure secondaire, morphing, recuit : robuste après un docking ou à basse résolution | Pondération statistique, B-factors atomiques, cartes Fo-Fc |
| Idéal pour | Toutes résolutions, affinements intermédiaires et finaux | Haute résolution (< 3 Å), détection d'erreurs et de ligands |

On peut utiliser les deux : Phenix pendant la construction, Servalcat pour l'affinement final et ses cartes de différence.

### 6.4 Ligands et eaux

1. **Ligand restraints (eLBOW)** à partir du code CCD (ou d'un SMILES) → sortie *restraints*.
2. Placez le ligand (Boltz-2, Coot, ISOLDE), fusionnez si besoin (**Merge models**).
3. Branchez les *restraints* sur **Real-space refinement** ou **Servalcat**.
4. Vérifiez la carte Fo-Fc de Servalcat : pas de pic négatif fort sur le ligand.
5. Sous ~2,5–3 Å : **Add waters (phenix.douse)**, puis inspection et réaffinement.

### 6.5 Sessions interactives en pratique

- **Open on server** ouvre ISOLDE/Coot sur l'écran configuré (`[interactive] display`, par exemple un bureau TurboVNC).
- **Download session bundle** (la bonne option depuis un portable) : un zip prêt à lancer sur votre poste (`chimerax isolde_session.py` ou `./run_coot.sh`) ;
  renvoyez le modèle avec **Upload model**.
- Sauvegardez dans le dossier du job (ISOLDE : `save isolde_model.cif models #1`), puis **Finish** :
  le modèle devient la sortie du job et les jobs en attente démarrent.

---

## 7. Étape 4 — Valider

### 7.1 Quels jobs ?

- **Map-model validation (Q-score, FSC)** : toujours (intégré, rapide). Repère les résidus mal soutenus.
- **Comprehensive validation (Phenix)** : géométrie MolProbity + accord carte–modèle, comme le rapport wwPDB.
- **MolProbity** seul entre deux sessions de correction ; **EMRinger** pour les chaînes latérales (< 4,5 Å).
- **Sequence register check (checkMySequence)** : avant tout dépôt. Détecte les décalages de registre (séquence
  glissée de quelques résidus le long de la chaîne), invisibles pour MolProbity et peu visibles dans les scores
  carte–modèle, ainsi que les chaînes qui ne correspondent à aucune séquence fournie.
- **wwPDB validation report (OneDep)** : le rapport officiel (PDF + XML) calculé par le serveur du wwPDB à partir du
  modèle et de la carte primaire, avec les centiles par rapport à toutes les entrées de la PDB. À lancer sur le
  modèle final ; le modèle et la carte sont envoyés au wwPDB.

### 7.2 Valeurs de référence

Elles dépendent de la résolution ; ce sont des ordres de grandeur pour un modèle bien affiné.

| Métrique | Bon | À examiner | Remarque |
|---|---|---|---|
| MolProbity score | ≤ 2,0 | > 3,0 | Combine clashs, Rama, rotamères |
| Clashscore | ≤ 10 (≤ 5 à haute résolution) | > 20 | Recalculé avec hydrogènes |
| Ramachandran favorisés | ≥ 95 % | < 90 % | Sans restreintes Rama pour être significatif |
| Ramachandran outliers | ≤ 0,5 % | > 2 % | Chacun doit être justifié par la densité |
| Rotamères outliers | ≤ 1–2 % | > 3 % | |
| Déviations Cβ | 0 | > 5 | |
| RMSD liaisons / angles | ≈ 0,002–0,01 Å / 0,5–1,5° | > 0,02 Å / > 2° | Trop bas peut signaler des restreintes trop fortes |
| CC (mask) | ≥ 0,7 (≥ 0,8 sous 3 Å) | < 0,5 | Dépend de la résolution et du masque |
| EMRinger | ≥ 2 (sous 4 Å) | ≈ 1 ou moins | Proche de 0 : registre ou main douteux |
| Inclusion d'atomes | ≥ 80 % | < 60 % | Au niveau de contour recommandé |
| FSC carte–modèle (0,5) | ≈ résolution FSC demi-cartes (0,143) | Bien meilleure que les demi-cartes | Une FSC carte–modèle « trop bonne » suggère un surajustement |

**Q-score attendu** (Pintilie et al. 2020) : Q ≈ −0,1775 × résolution + 1,1192.

| Résolution | 2,0 Å | 2,5 Å | 3,0 Å | 3,5 Å | 4,0 Å |
|---|---|---|---|---|---|
| Q-score attendu | 0,76 | 0,68 | 0,59 | 0,50 | 0,41 |

Un Q-score moyen proche de la valeur attendue indique un bon accord global ; les résidus avec Q < 0,3
sont à revoir (ou situés dans des régions de basse résolution locale).

### 7.3 Signaux d'alerte

| Symptôme | Cause probable | Action |
|---|---|---|
| EMRinger proche de 0, hélices gauches | Main inversée | Flip handedness puis reconstruire |
| Bon CC, mauvaise géométrie | Surajustement, restreintes trop faibles | ISOLDE, restreintes de structure secondaire |
| Bonne géométrie, CC/Q-score faibles | Modèle mal placé ou registre décalé | Vérifier dans la carte, fitmap, ISOLDE |
| Q-score bas dans une région seulement | Résolution locale faible ou erreur locale | Comparer avec la carte LocScale ; corriger ou retirer les atomes non soutenus |
| FSC carte–modèle meilleure que la FSC demi-cartes | Ajustement du bruit | Affinement moins agressif, cible demi-cartes (Servalcat) |
| Beaucoup de résidus UNK | Séquence absente ou mal attribuée | Identifier les chaînes, reconstruire avec la séquence |
| checkMySequence signale un décalage | Registre décalé de quelques résidus | Corriger dans Coot ou ISOLDE comme indiqué, relancer la vérification |

---

## 8. Étape 5 — Déposer

### 8.1 Pre-deposition checks

Le job vérifie automatiquement : cohérence cartes / demi-cartes / masque (boîte, pixel, origine),
atomes dans la boîte, CC et inclusion au contour suggéré, identifiants de chaîne, résidus inconnus,
occupations nulles, B-factors non affinés ou de type pLDDT, chevauchements sévères, ruptures de chaîne,
identité avec la séquence. **Corrigez chaque FAIL** ; chaque WARN doit être compris.

### 8.2 Deposition package

Le paquet contient `model.cif`, `primary_map.mrc`, `half_map_1/2.mrc`, `mask.mrc`, `fsc.xml`, les rapports
de validation, `CHECKLIST.md`, `methods_draft.md` et `table1_draft.md`.

### 8.3 OneDep pas à pas

1. Lancez d'abord le job **wwPDB validation report** (ou le serveur validate.wwpdb.org) avec le modèle et la carte primaire.
2. Dans OneDep (deposit.wwpdb.org) : déposez le modèle (mmCIF), la carte primaire, les deux demi-cartes,
   le masque et la courbe FSC.
3. Saisissez les valeurs du fichier `CHECKLIST.md` : niveau de contour recommandé, résolution, taille de pixel, symétrie.
4. Les cartes LocScale, de modification de densité ou spIsoNet peuvent être ajoutées comme **cartes additionnelles**, avec leur description.
5. La liste des logiciels et leurs versions se trouve dans `methods_draft.md` et dans les logs (`commands.sh` de chaque job).

### 8.4 Pour l'article

`methods_draft.md` contient un paragraphe *Méthodes* et les citations générés à partir de l'historique
des jobs ; `table1_draft.md` pré-remplit le tableau de statistiques cryo-EM. Complétez les données de
collecte (tension, dose, défocus, nombre de particules) et relisez tout.

---

## 9. Recettes par situation

### A. Protéine membranaire à 3 Å, séquence connue

1. Import from CryoSPARC (+ symétrie) · Import sequence
2. Directional resolution
3. LocScale 2 (model-free) — la micelle et la périphérie deviennent lisibles
4. ModelAngelo build et CryoAtom2 build sur la carte LocScale ; comparer avec Map-model validation
5. Real-space refinement → ISOLDE → Real-space refinement (final, contre `map_sharp`)
6. Comprehensive validation · Map-model validation · Pre-deposition checks · Deposition package

Raccourci : workflow *De novo model → deposition (ModelAngelo ou CryoAtom2)*.

### B. Complexe à 3,8 Å avec régions flexibles

1. Import · LocScale (model-free) ou modification de densité Phenix
2. Modèles prédits : AlphaFold DB, ColabFold ou Boltz-2 (complexe entier)
3. Process predicted model (découpe en domaines si nécessaire)
4. Rigid-body fit (recherche globale) ou Dock in map, domaine par domaine → Merge models
5. Real-space refinement avec morphing → ISOLDE (restreintes adaptatives) → affinement final
6. LocScale en mode hybride avec le modèle pour la carte d'interprétation finale

Raccourci : workflow *Predicted model → deposition (AlphaFold)*.

### C. Densité inconnue dans un complexe natif

1. ModelAngelo build **sans séquence**
2. Identify chains (HMM search) contre le protéome de l'organisme
3. CryoAtom2 build avec les séquences identifiées (ou CryoAtom2 avec la base de séquences directement)
4. Vérifier l'accord séquence/densité (chaînes latérales aromatiques, glycines, prolines)

Raccourci : workflow *Identify unknown proteins in the map*.

### D. Complexe protéine–ARN

1. Import sequence avec les enregistrements protéine et ARN (séparés automatiquement)
2. CryoAtom2 build (protéine, ARN et ADN passés séparément)
3. ISOLDE pour les appariements et la géométrie des nucléotides
4. Affinement, validation, dépôt

### E. Ligand ou médicament à 2,8 Å

1. Modèle de la protéine (de novo ou prédit) affiné
2. Boltz-2 avec le ligand (CCD ou SMILES) pour une pose initiale, ou placement dans Coot/ISOLDE
3. Ligand restraints (eLBOW) → Real-space refinement avec les restreintes
4. Servalcat : vérifier la carte Fo-Fc autour du ligand
5. Add waters si la résolution le permet ; validation et dépôt (code CCD officiel si le ligand existe)

### F. Orientation préférentielle sévère

1. Directional resolution : rapport > 1,6, pire direction le long d'un axe
2. spIsoNet (demi-cartes + masque doux)
3. Construction et affinement sur la carte corrigée, en restant critique dans la direction mal résolue
4. Dépôt : cartes d'origine en carte primaire et demi-cartes ; carte spIsoNet en carte additionnelle, mentionnée dans les méthodes

### G. Gros complexe résolu par raffinements locaux

1. Import CryoSPARC de la carte consensus et de chaque raffinement local
2. Local resolution sur la carte consensus : repérer les régions faibles ; si besoin, Create mask (densité proche
   des chaînes de la région) pour de nouveaux raffinements locaux dans CryoSPARC
3. Modèle ajusté dans la consensus (prédictions, Rigid-body fit ou Dock in map, Merge models)
4. Composite map (consensus + cartes locales, demi-cartes combinées)
5. Real-space refinement dans la carte composite → ISOLDE → affinement final
6. Local resolution sur les demi-cartes composites (résolution de chaque sous-unité), checkMySequence,
   Map-model validation, wwPDB validation report, Pre-deposition checks, Deposition package

### H. Domaine flexible ou sous-unité partiellement présente

1. Import CryoSPARC du raffinement consensus (cartes et particules) ; Local resolution pour localiser la région floue
2. Workflow *Conformational heterogeneity (cryoDRGN)* : contrôle des entrées, tour 1 à 128 px (petit réseau)
3. Galerie et explorateur : repérer le junk (volumes *weak / noisy / blurry*, outliers, images des particules) →
   *Remove…* → tour 2 sur la sélection à 256 px (grand réseau automatique)
4. *cryoDRGN convergence check* ; *continue training* jusqu'à 100 époques si besoin
5. Volume series analysis des clusters avec le modèle : chaînes qui bougent ou qui disparaissent ; *Trajectory…* entre
   deux états pour la figure ou la vidéo ; *landscape analysis* pour définir des états
6. *Keep…* pour chaque état, retour à CryoSPARC (Import Particle Stack → NU refinement) : une carte gold-standard par
   état, puis la suite du pipeline (construction, affinement, validation) pour chacune
7. Si la 3DVA est déjà calculée : workflow *3D variability (CryoSPARC) → interpretation* pour comparer

---

## 10. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| Le job échoue immédiatement | Programme introuvable | Page **Tools** : configurez `setup` / `bin_dir` / `executable`, redémarrez, *Re-check* |
| « Resolution is unknown » | Pas de FSC à l'import | Renseignez le paramètre *Resolution* |
| Job bloqué en `queued` | Parent pas fini, slot ou GPU occupé, parent en échec | Lisez le message de la carte (« Waiting for J3 », « Blocked: J3 failed ») et **Resources › Queue** |
| « Waiting for GPU 1 (used by P2/J5) » | Le GPU choisi pour ce job est pris par un autre job | Attendre, ou *Dequeue* puis *Edit* : GPU *Automatic* ou un autre GPU |
| Aucun GPU dans **Resources** | `nvidia-smi` absent du PATH du serveur, ou pilote NVIDIA en panne (message affiché) | Indiquer son chemin : `[monitor] nvidia_smi = "/usr/bin/nvidia-smi"` dans la configuration, puis redémarrer |
| Lane SLURM : *Test* répond « sinfo: command not found » | Les commandes SLURM ne sont pas accessibles au service CryoPlug | Installer les clients SLURM sur le serveur (ou lancer CryoPlug sur un nœud de connexion du cluster), avec le même PATH que le service |
| Job SLURM qui reste en attente | Partition pleine, durée ou mémoire demandées trop grandes | Onglet **Cluster** de *Resources* ; demander moins, ou une autre partition dans *Compute* |
| Erreur mémoire GPU | Carte trop grande ou batch trop gros | Réduire la taille de batch, recadrer la boîte, utiliser un GPU plus gros |
| ModelAngelo : fragments, hélices gauches | Main inversée | Map operations → Flip handedness |
| Modèle décalé par rapport à la carte | Carte non cubique donnée à ModelAngelo | Map operations → recadrer en boîte cubique |
| ISOLDE/Coot ne s'ouvre pas depuis le navigateur | Pas d'affichage configuré | `[interactive] display` dans la configuration, ou paquet de session |
| L'interface ne s'ouvre pas depuis le portable | Serveur en écoute locale (`127.0.0.1`) ou port bloqué par le pare-feu | `host = "0.0.0.0"` dans `[server]` puis redémarrer ; ouvrir le port (`sudo ufw allow 39500/tcp`) ; ou tunnel SSH |
| « Wrong access token » à la connexion | Jeton mal copié ou renouvelé | `cryoplug url` sur le serveur réaffiche le lien de connexion |
| Le visualiseur 3D reste vide | Mol* non installé et pas d'accès internet | `cryoplug fetch-viewer` sur le serveur |
| Le visualiseur affiche « The 3D view could not start » | WebGL désactivé dans le navigateur | Activer l'accélération matérielle du navigateur |
| Carte pleine de bruit dans le visualiseur | Seuil trop bas ou boîte très grande | Monter le seuil (+, ou histogramme), cocher **Zone** autour du modèle |
| « The program finished but wrote no map / model in J… » | Le programme s'est arrêté sur une erreur sans signaler d'échec | Cherchez sa dernière erreur dans le log, corrigez, puis **Clear** et relancez |
| Avertissement « … was written next to an input … moved to … » dans le log | Le programme a écrit son résultat dans le dossier d'un autre job (celui de ses entrées) | Rien à faire : CryoPlug a ramené le fichier dans le dossier du job, l'autre job reste intact |
| Coot / ISOLDE : « No saved model found in the job folder » | Modèle enregistré dans un autre dossier | Enregistrez-le dans le dossier du job (chemin donné par le message) ou envoyez-le (*Upload*), puis *Finish* |
| Create mask : « Masks need SciPy » | SciPy absent de l'environnement de CryoPlug | `pip install scipy` (installé avec CryoPlug depuis cette version) |
| wwPDB validation : « wwPDB validation server: … » | Serveur indisponible, pas d'accès internet depuis le serveur, fichier refusé | Vérifier l'accès internet (proxy) du serveur, relancer plus tard ; le message du serveur est dans le log |
| checkMySequence : « needs HMMER » | `hmmsearch` absent de son environnement | `conda install -c bioconda hmmer` dans l'environnement de checkMySequence |
| cryoDRGN : volumes creux, junk partout, mémoire, convergence… | — | Tableau des pièges de cryoDRGN : §4.11 |
| cryoDRGN : « Cannot find file … under datadir » | Le projet CryoSPARC a été déplacé, ou les chemins des images ne sont pas relatifs au dossier du projet | *Import particles* avec *Image folder* = le dossier du projet CryoSPARC qui contient les images |
| « These particles have no poses » | Particules d'une extraction ou d'une classification 2D | Importez le job de **raffinement** (champs `alignments3D`) |
| « … uses CryoSPARC's compressed format » | Fichiers `.cs` compressés des versions récentes de CryoSPARC | `pip install cryosparc-tools` dans l'environnement de CryoPlug |
| Import d'un job 3DVA : « only the 3D variability coordinates are imported » | Le fichier `passthrough` du job manque | L'explorateur fonctionne ; pour entraîner ou réutiliser les particules, importez le raffinement consensus |
| La série se charge lentement dans le visualiseur | Beaucoup de volumes, carte graphique logicielle | Normal la première fois (chaque volume est chargé une fois) ; la lecture est ensuite fluide |
| Relancer un job à l'identique hors CryoPlug | — | `commands.sh` dans le dossier du job contient les commandes exactes |

Le **log** (onglet *Log*) montre la commande exécutée, la sortie du programme et l'erreur en rouge.
Après correction : **Clear** remet le job en préparation, **Clone** crée une copie modifiable.

---

## 11. Bonnes pratiques

- Un projet par structure ; donnez des **titres** parlants aux jobs et utilisez les **notes**.
- Ne supprimez pas un job utilisé par d'autres (CryoPlug le signale) ; préférez *Clone* pour essayer une variante.
- Gardez toujours les demi-cartes et le masque d'origine : ils sont nécessaires à la validation et au dépôt.
- Notez les versions des logiciels (affichées dans les logs) ; elles sont demandées au dépôt.
- Sauvegardez le dossier du projet (il contient tout : paramètres, logs, fichiers).
- Ne concluez jamais sur une seule carte issue d'un réseau de neurones.

---

## 12. Glossaire

| Terme | Définition |
|---|---|
| FSC gold-standard | Corrélation entre deux demi-cartes reconstruites indépendamment, coquille par coquille de fréquence ; la résolution est lue au seuil 0,143. |
| Randomisation de phase | Correction de l'effet du masque sur la FSC (Chen et al. 2013). |
| 3D FSC / FSC conique | FSC calculée dans un cône autour de chaque direction : révèle l'anisotropie. |
| Affûtage (sharpening) | Remontée des hautes fréquences (B-factor négatif) pour révéler les détails. |
| Affûtage local | Affûtage adapté à la résolution locale (LocScale, local_aniso_sharpen). |
| Q-score | Ressemblance de la densité autour d'un atome avec un profil gaussien de référence (0 à 1). |
| CC (mask) | Corrélation entre la carte et une carte calculée depuis le modèle, dans une enveloppe autour du modèle. |
| EMRinger | Score basé sur la position des pics de densité autour des chaînes latérales. |
| pLDDT / ipTM | Confiance par résidu / confiance de l'interface d'une prédiction AlphaFold/Boltz. |
| Fo-Fc | Carte de différence entre observation et modèle : pics positifs = densité non modélisée, négatifs = atomes en trop. |
| OneDep | Système de dépôt commun wwPDB / EMDB. |
| Espace latent | Coordonnées apprises par cryoDRGN pour chaque particule : des particules proches ont des structures proches. |
| Époque | Un passage complet de l'entraînement sur toutes les particules. |
| z, dimension latente | Nombre de coordonnées latentes par particule (8 par défaut dans cryoDRGN). |
| β, divergence KL | Terme qui régularise l'espace latent ; β (1/z par défaut) règle son poids. |
| Rétroprojection | Reconstruction directe des images avec leurs poses, sans affinement : sert à contrôler les entrées. |
| Convergence | État d'un entraînement dont les volumes et l'espace latent ne changent plus d'une époque à l'autre. |
| Sur-apprentissage | Le réseau apprend le bruit des images : volumes de plus en plus bruités aux dernières époques. |
| Junk | Particules sans intérêt (agrégats, glace, particules cassées ou mal centrées) à retirer. |
| Volumes « on-data » | Volumes générés aux coordonnées de vraies particules, jamais dans des régions vides de l'espace latent. |
| Paysage (landscape) | Analyse de cryoDRGN qui regroupe en états des centaines de volumes générés, comparés entre eux. |
| UMAP | Projection en 2D d'un espace à plusieurs dimensions qui préserve les voisinages (les distances entre îlots ne se lisent pas). |
| 3DVA | *3D Variability Analysis* de CryoSPARC : composantes linéaires de la variabilité et coordonnée de chaque particule le long de chacune. |
| Série de volumes | Volumes ordonnés (clusters, trajectoire, frames de 3DVA) superposés, joués comme un film. |
| Carte de variabilité | Écart-type de la densité entre les volumes d'une série : grand là où la structure change. |
