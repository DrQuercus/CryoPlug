# CryoPlug

**Suite de post-traitement cryo-EM dans le navigateur, de la meilleure carte CryoSPARC jusqu'au dépôt PDB/EMDB.**

CryoPlug prend le relais une fois la reconstruction terminée dans CryoSPARC. Il pilote, depuis une
interface web inspirée de CryoSPARC (projets, cartes de jobs, file d'attente, lanes, **comptes
utilisateurs**), tous les logiciels installés sur votre serveur local :

| Étape | Logiciels pilotés | Jobs CryoPlug |
|---|---|---|
| Import | CryoSPARC, RELION, fichiers MRC, PDB, AlphaFold DB, UniProt | `Import from CryoSPARC`, `Import maps`, `Import atomic model`, `Import sequence` |
| Amélioration de carte | **LocScale 2** (model-free / pseudo-modèle / model-based / hybride), **EMmerNet**, **DeepEMhancer**, **EMReady**, **spIsoNet** (correction de l'anisotropie due à l'orientation préférentielle), Phenix `resolve_cryo_em`, `auto_sharpen`, `local_aniso_sharpen`, **résolution locale** (`local_resolution`), **cartes composites** de raffinements locaux (`combine_focused_maps`) | + FSC demi-cartes (masquée, corrigée par randomisation de phase), **résolution directionnelle / 3D FSC**, **création de masques** (densité, chaînes du modèle, densité proche du modèle ; dilatation et bord doux en Å) et opérations de carte (main, B-factor, filtre, masque, boîte) intégrées |
| Hétérogénéité (variabilité 3D) | **cryoDRGN** (entraînement, analyse, trajectoires), **CryoSPARC 3D Variability** et *3D Variability Display* / 3D Flex (import des coordonnées des particules et des séries de volumes) | **Explorateur de l'espace latent** interactif (UMAP / PCA, clusters → volumes en 3D, sélection de particules, trajectoire), **lecture des séries** dans le visualiseur 3D, **analyse de série** (carte de variabilité, corrélation entre états, chaînes qui bougent ou disparaissent), sélection de particules vers CryoSPARC ou un nouvel entraînement |
| Construction de modèle | **ModelAngelo** (avec ou sans séquence) et **identification de chaînes inconnues** (`hmm_search`), **CryoAtom2** (protéines, ARN/ADN, complexes, identification par base de séquences), ColabFold/AlphaFold2, **Boltz-2** (complexes protéines/acides nucléiques/ligands), Phenix `process_predicted_model`, `dock_in_map`, ChimeraX `fitmap` | + restreintes de ligands (Phenix eLBOW), fusion et édition de modèles (gemmi) |
| Reconstruction interactive | **ISOLDE** (ChimeraX), **Coot** | sessions ouvertes en un clic sur l'écran du serveur, ou paquet téléchargeable pour votre poste |
| Affinement | Phenix `real_space_refine`, **Servalcat** (demi-cartes, cartes Fo-Fc), `phenix.douse` (eaux) | restreintes de ligands branchées directement sur l'affinement |
| Validation | MolProbity, EMRinger, Phenix `validation_cryoem`, **checkMySequence** (erreurs de registre de séquence), **rapport officiel wwPDB** (service de validation OneDep) | + **Q-score**, FSC carte-modèle, CC_mask, inclusion d'atomes intégrés (sans logiciel externe) |
| Dépôt | — | **Checklist pré-dépôt** automatique et **paquet OneDep** : mmCIF, cartes, FSC XML, niveau de contour recommandé, brouillon de *Méthodes* avec citations et de « Table 1 » |

![Jobs d'un projet](docs/images/jobs.png)

| Constructeur de job (pré-rempli, glisser-déposer) | Résolution directionnelle (3D FSC) |
|---|---|
| ![](docs/images/builder.png) | ![](docs/images/directional_fsc.png) |
| **Session ISOLDE prête** (ouvrir sur le serveur ou télécharger le paquet) | **Graphe du pipeline** |
| ![](docs/images/isolde.png) | ![](docs/images/graph.png) |
| **Workflows** | **Checklist avant dépôt** |
| ![](docs/images/workflows.png) | ![](docs/images/checklist.png) |
| **Visualiseur 3D** : panneau de modèles, histogramme de seuil, zone autour du modèle, ligne de commande | **Coupes 2D** (et thème sombre) |
| ![](docs/images/viewer.png) | ![](docs/images/viewer_slices.png) |
| **Lignée du job ouvert** : ses entrées en violet, les jobs qui l'utilisent en vert | **Mode sombre** |
| ![](docs/images/lineage.png) | ![](docs/images/dark.png) |

| Validation carte-modèle (Q-score, FSC) | Paquet de dépôt (méthodes, Table 1) | Boltz-2 (complexe + ligands) |
|---|---|---|
| ![](docs/images/validation.png) | ![](docs/images/package.png) | ![](docs/images/boltz.png) |

| **Résolution locale** dans le visualiseur (bleu = meilleure, échelle réglable) | **Création de masque** : densité proche des chaînes choisies, coupes de contrôle |
|---|---|
| ![](docs/images/viewer_locres.png) | ![](docs/images/mask.png) |
| **Résolution locale par chaîne et par résidu** | **Registre de séquence** (checkMySequence) |
| ![](docs/images/local_resolution.png) | ![](docs/images/checkmysequence.png) |

| **Explorateur de l'espace latent** (cryoDRGN) : clusters choisis → *Play in 3D*, *Keep…*, *Remove…*, *Trajectory…* | **Série de volumes** jouée dans le visualiseur (barre de lecture, même seuil pour tous) |
|---|---|
| ![](docs/images/latent_explorer.png) | ![](docs/images/viewer_series.png) |
| **Carte de variabilité** sur la carte moyenne (bleu = stable, rouge = variable) | **Analyse de série** : densité de chaque chaîne dans chaque volume (une hélice disparaît) |
| ![](docs/images/viewer_variability.png) | ![](docs/images/series_analysis.png) |

| **Comptes utilisateurs** (Settings › Users) : rôle, dossier des projets, dossiers lisibles, statut | **Ajout d'un utilisateur** : mot de passe généré ou choisi, dossiers autorisés |
|---|---|
| ![](docs/images/accounts_users.png) | ![](docs/images/accounts_add_user.png) |

*Captures réalisées avec les jeux de données synthétiques de démonstration et des programmes de substitution
(aucun calcul réel de ModelAngelo, CryoAtom2, Phenix, cryoDRGN… dans ces images).*

> 📘 **Nouveau sur CryoPlug ?** Lisez le [**guide complet**](cryoplug/web/docs/GUIDE.md) (quelle carte pour quoi,
> choix des méthodes, valeurs de validation, dépôt, recettes, dépannage) et la
> [**référence des jobs**](docs/JOBS.md) (à quoi sert chaque job et quand l'utiliser). Les deux sont aussi
> intégrés dans l'interface : page **Help**, bouton **?** du constructeur de jobs, section « À propos de ce
> job » du panneau de détails.

| Guide intégré | Référence des jobs (recherche par situation) | Aide dans le constructeur |
|---|---|---|
| ![](docs/images/guide.png) | ![](docs/images/job_reference.png) | ![](docs/images/builder_help.png) |

---

## Sommaire

1. [Installation sur le serveur](#1-installation-sur-le-serveur)
2. [Configurer les logiciels](#2-configurer-les-logiciels)
3. [Utilisation](#3-utilisation)
4. [Lanes, GPU et cluster SLURM](#4-lanes-gpu-et-cluster-slurm)
5. [Comptes utilisateurs, accès réseau et sécurité](#5-comptes-utilisateurs-accès-réseau-et-sécurité)
6. [Référence des jobs](#6-référence-des-jobs)
7. [Architecture et ajout d'un nouveau logiciel](#7-architecture-et-ajout-dun-nouveau-logiciel)
8. [Tests](#8-tests)

---

## 1. Installation sur le serveur

Prérequis : Linux, Python ≥ 3.10, et les logiciels cryo-EM que vous voulez piloter (chacun peut vivre
dans son propre environnement conda / module, voir §2).

```bash
git clone https://github.com/DrQuercus/CryoPlug.git
cd CryoPlug
python3 -m venv ~/cryoplug-venv
~/cryoplug-venv/bin/pip install .

~/cryoplug-venv/bin/cryoplug init          # écrit ~/.cryoplug/config.toml (commenté)
nano ~/.cryoplug/config.toml               # déclarer vos logiciels (§2)
~/cryoplug-venv/bin/cryoplug tools         # vérifier ce qui est détecté
~/cryoplug-venv/bin/cryoplug fetch-viewer  # installe le visualiseur 3D Mol* en local (fonctionne hors-ligne)
~/cryoplug-venv/bin/cryoplug start         # serveur web + planificateur de jobs
```

Ouvrez ensuite **http://localhost:39500** sur le serveur. Pour l'utiliser à plusieurs depuis d'autres postes,
créez les comptes dans **Settings › Users** et ouvrez le port : voir
[§5](#5-comptes-utilisateurs-accès-réseau-et-sécurité) (`host = "0.0.0.0"`, comptes comme dans CryoSPARC).

Pour essayer sans vraies données :

```bash
cryoplug demo-data ~/cryoplug_demo   # faux dossier de job CryoSPARC (J42), modèle et FASTA
```

puis *New project → Import from CryoSPARC →* dossier `~/cryoplug_demo/CS-demo/J42`.

### Service systemd (démarrage automatique)

```bash
cryoplug service | sudo tee /etc/systemd/system/cryoplug.service
sudo systemctl daemon-reload && sudo systemctl enable --now cryoplug
```

`KillMode=process` est utilisé : redémarrer le service **ne tue pas** les jobs en cours (ils tournent dans
leurs propres processus et sont retrouvés au redémarrage). Un exemple est aussi fourni dans
[`deploy/cryoplug.service`](deploy/cryoplug.service).

## 2. Configurer les logiciels

Chaque logiciel est lancé dans un `bash` après ses lignes de `setup` (source, conda, module), avec un
`bin_dir` ajouté au `PATH` et des variables d'environnement optionnelles. Rien n'a besoin d'être dans le
même environnement Python que CryoPlug.

```toml
[tools.phenix]
setup = "source /opt/phenix-1.21.2/phenix_env.sh"

[tools.modelangelo]
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate model_angelo"

[tools.cryoatom]            # CryoAtom2 (installé par son install.sh dans l'env conda CryoAtom2)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate CryoAtom2"

[tools.locscale]            # LocScale 2 et EMmerNet
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate locscale"

[tools.spisonet]
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate spisonet"

[tools.boltz]               # pip install boltz
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate boltz"

[tools.deepemhancer]
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate deepEMhancer_env"

[tools.emready]
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate emready"

[tools.servalcat]
setup = "source /opt/ccpem/setup_ccpem.sh"

[tools.chimerax]            # ChimeraX + ISOLDE (installé depuis le Toolshed)
executable = "/usr/bin/chimerax"

[tools.coot]
executable = "/opt/coot/bin/coot"

[tools.colabfold]
bin_dir = "/opt/localcolabfold/colabfold-conda/bin"

[tools.checkmysequence]     # conda env avec hmmer + checkMySequence (gitlab.com/gchojnowski/checkmysequence)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate checkmysequence"

[tools.onedep]              # client du serveur de validation wwPDB : pip install onedep_api (accès internet requis)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate onedep"

[tools.cryodrgn]            # cryoDRGN 3.x/4.x : pip install cryodrgn dans son env (GPU)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate cryodrgn"

[interactive]
display = ":0"   # écran (ou bureau TurboVNC, ex. ":1") où ouvrir Coot / ISOLDE depuis le navigateur
```

La page **Tools** de l'interface montre l'état de détection, le chemin et la version de chaque programme.
Chaque job a aussi un champ *Extra arguments* (paramètres avancés) pour passer n'importe quelle option
supplémentaire, et le job **Custom command** permet d'intégrer un logiciel non prévu en quelques secondes.

Les fichiers de particules `.cs` des versions récentes de CryoSPARC (format compressé) se lisent avec
`cryosparc-tools` : `~/cryoplug-venv/bin/pip install cryosparc-tools` (inutile pour le format NumPy classique).

> Les lignes de commande suivent la documentation et le code source actuels de chaque logiciel
> (ModelAngelo 1.x, CryoAtom2 v2.1, LocScale 2, spIsoNet 1.0, Boltz-2, DeepEMhancer, EMReady, Servalcat
> `refine_spa_norefmac`, Phenix 1.21, cryoDRGN 4.x). Si votre version
> diffère, utilisez *Extra arguments* ou adaptez le job correspondant (§7) ; la commande exacte exécutée
> est toujours enregistrée dans `commands.sh` et dans le log du job.

## 3. Utilisation

### Concepts (comme CryoSPARC)

- **Projet** = un dossier `CP-<titre>` ; chaque **job** écrit dans son sous-dossier `J<n>`.
- Un job consomme les **sorties** d'autres jobs (demi-cartes, carte, masque, modèle, séquence, FSC, rapport).
- Statuts : `building` → `queued` → `launched/running` → `completed` / `failed` / `killed` ; les jobs
  interactifs passent par `waiting`.
- On peut mettre toute une chaîne en file d'attente d'un coup : chaque job attend que ses parents soient
  terminés, puis qu'un slot de lane et un GPU soient libres.
- La **résolution « auto »** est propagée depuis l'import (FSC = 0.143) : inutile de la ressaisir partout.

### Pipeline typique

1. **New project**, puis **Import from CryoSPARC** : indiquez le dossier du job de raffinement
   (ex. `/data/CS-monprojet/J245`). Demi-cartes, cartes non affinée et affinée, masque FSC et carte de
   résolution locale sont trouvés automatiquement (dernière itération) et la FSC est recalculée.
2. **Continue with…** sur le job d'import propose tous les jobs compatibles, pré-remplis
   (par ex. LocScale 2 avec les demi-cartes et le masque).
3. Pendant que le constructeur de job est ouvert, les **pastilles de sortie** apparaissent sur les cartes
   de jobs : glissez-les sur les entrées. Cliquer sur un job l'ouvre dans la zone principale (comme dans
   CryoSPARC) sans fermer le constructeur : glissez ses sorties sur les entrées, ou « Use as input » ;
   « Jobs » revient aux cartes.
4. **Workflows** crée une chaîne complète en un clic (de novo avec ModelAngelo ou CryoAtom2, modèle prédit
   AlphaFold, identification de protéines inconnues, comparaison des méthodes d'amélioration de carte,
   validation et dépôt), par exemple *De novo model → deposition (ModelAngelo)* :
   import → LocScale → ModelAngelo → real_space_refine → ISOLDE → affinement final → validation →
   checklist → paquet de dépôt. Les étapes optionnelles (ISOLDE, LocScale…) peuvent être retirées, les
   jobs en aval sont recâblés automatiquement.
5. La vue **Graph** montre le pipeline ; chaque job a ses onglets *Overview* (métriques, graphes FSC /
   Q-score, sorties, paramètres, notes), *Log* (en direct) et *Files*. Quand un job est ouvert, ses
   **entrées** sont entourées en violet sur les cartes et les **jobs qui l'utilisent** en vert.
6. **View 3D** (dans les sorties d'un job, ou bouton **3D** au survol d'une carte de job) ouvre le
   visualiseur décrit ci-dessous.

### Actions, sélection multiple et raccourcis (comme CryoSPARC)

- Le bouton **⋯** d'une carte de job (ou un **clic droit**) ouvre son menu : ouvrir, *Continue with…*,
  voir en 3D, cloner, réinitialiser, copier le chemin du dossier, supprimer. Dans le détail d'un job,
  l'action principale (*Queue*, *Continue with…*, *Stop*…) reste en évidence et les autres sont dans le
  même menu **⋯**.
- En haut à droite, le **résumé** compte les jobs par statut (en cours, en file, en attente, échoués) :
  un clic sur un statut n'affiche que ces jobs, un second clic revient à tous.
- **Ctrl/⌘ + clic** ajoute un job à la sélection, **Maj + clic** une plage, **Ctrl/⌘ + A** tous les jobs
  affichés ; la barre de sélection propose **Queue**, **Stop**, **Clear** et **Delete** (avec le nombre de
  jobs concernés par chaque action et une confirmation).
- **N** nouveau job, **/** filtrer, **G** cartes ⇄ graphe, **Échap** vide la sélection ou ferme le détail,
  **?** liste des raccourcis.

### Visualiseur 3D

Une interface entre CryoSPARC et ChimeraX, dessinée par Mol\* (qui fonctionne hors-ligne après
`cryoplug fetch-viewer`) :

- **Panneau Models** à droite : chaque carte ou modèle porte un numéro (`#1`, `#2`…), un œil pour le
  masquer et une pastille de couleur. Pour une carte : **histogramme** des densités (échelle log) dont on
  fait glisser le trait pour régler le **seuil**, saisie en valeur absolue ou en **σ** avec le pourcentage
  de voxels au-dessus, style **Surface / Mesh / Transparent**, et **Zone** : n'afficher la densité qu'à
  moins de *r* Å du modèle (comme `volume zone` de ChimeraX, calculée sur le serveur). Pour un modèle :
  **Cartoon**, **Cartoon + chaînes latérales** ou **Sticks**, couleur par chaîne, structure secondaire,
  élément, B-factor ou arc-en-ciel, et « Go to » `A:45`.
- **Souris** : clic gauche-glisser tourne, clic droit (ou Ctrl + gauche) déplace, molette zoome,
  Maj + molette déplace les plans de coupe ; **cliquer sur un atome recentre la vue sur son résidu**
  (comme Coot) et la barre d'état affiche l'atome survolé (`/A LYS 45 CA · B 32.1`).
- **Barre d'outils** : réinitialiser la vue, rotation continue, *slab* (tranche autour du centre),
  projection orthographique, éclairage doux (occlusion ambiante), fond noir / gris / blanc, **coupes 2D**
  XY/XZ/YZ de la carte active (comme CryoSPARC, voxels au-dessus du seuil teintés), image PNG à 2× la
  taille de la fenêtre. **Open…** ajoute d'autres cartes, masques, demi-cartes ou modèles du projet.
- **Ligne de commande** façon ChimeraX (↑/↓ pour l'historique, `help` pour la liste) :
  `level 3σ`, `style mesh`, `color #2 bychain`, `view /A:45`, `zone 3`, `volume #1 level 0.5 style mesh`,
  `bg white`, `slab 30`, `lighting soft`, `slices`, `fullres #1`, `save figure.png scale 3`…
- **Clavier** : **+ / −** seuil de la carte active (0,1 σ, Maj : 0,5 σ), **M** style suivant, **R** vue
  initiale, **S** rotation, **L** coupes, **:** ligne de commande, **?** aide.
- **Coloration par résolution locale** : menu *Colour* d'une carte (ou `color #1 localres 3 6`), échelle bleu
  (meilleure) → rouge (moins bonne) réglable avec sa légende ; le bouton *View 3D* d'un job *Local resolution* (ou
  d'un import CryoSPARC avec carte de résolution locale) ouvre directement la carte coloriée.
- **Coloration par variabilité** (sortie d'un job *Volume series analysis*) : même principe, en σ (bleu = stable, rouge =
  variable) ; `color #1 variability`.
- **Séries de volumes** (clusters et trajectoires cryoDRGN, frames de 3DVA) : *Play in 3D* charge tous les volumes puis
  les joue comme un film — barre de lecture sur la vue (lecture/pause, précédent/suivant, curseur, vitesse, boucle ou
  aller-retour), **Espace**, **,** et **.**, commandes `play 10`, `stop`, `frame 5`, `vseries play #1`. Le seuil est
  commun à tous les volumes.
- Les cartes arrivent en **aperçu binné** (au plus 200 voxels de côté, 128 pour les séries) pour la rapidité ; le menu
  **⋯** d'une carte (ou `fullres #1`) charge le fichier d'origine.

### Hétérogénéité et variabilité 3D (cryoDRGN, CryoSPARC 3DVA)

1. **Import from CryoSPARC** du raffinement consensus importe aussi ses **particules** (poses, CTF, emplacement des
   images ; les fichiers `particles` et `passthrough` sont fusionnés, les images restent dans le projet CryoSPARC).
2. **cryoDRGN training** (GPU ; 128 px pour un premier passage) produit l'espace latent, les volumes de 20 clusters
   et les trajectoires le long des composantes principales. Le rapport contient un **explorateur interactif** : nuage
   des particules en UMAP / PCA coloré par cluster ; cliquez des clusters pour les jouer en 3D, les garder ou les
   retirer (*Keep… / Remove…* préparent *Select particles*), suivre la transition (*Trajectory…*) ou en extraire la carte.
3. **Volume series analysis** interprète une série : carte de variabilité (affichée en couleur sur la carte moyenne),
   corrélation entre volumes (états distincts ou mouvement continu) et, avec un modèle, la densité de chaque chaîne
   dans chaque volume.
4. **Select particles** écrit un `.cs` pour CryoSPARC (*Import Particle Stack*) et les indices pour un nouvel
   entraînement cryoDRGN (qui réutilise les images déjà réduites).
5. Déjà calculé dans CryoSPARC : *Import from CryoSPARC* du job **3D Variability** (coordonnées des particules le long
   des composantes, mêmes explorateur et sélection) et **Import volume series** du job *3D Variability Display*
   (dossier, ZIP ou motif de fichiers ; une série par composante).

Workflows *Conformational heterogeneity (cryoDRGN)* et *3D variability (CryoSPARC) → interpretation* ; détails et
interprétation dans le [guide](cryoplug/web/docs/GUIDE.md) (§3.7 et recette H).

### Sessions interactives ISOLDE / Coot

Le job prépare la session (modèle + cartes associées, `clipper associate`, `isolde start`) puis passe en
`waiting` :

- **Open on server** ouvre ChimeraX/ISOLDE ou Coot sur l'affichage configuré (`[interactive] display`) ;
- **Download session bundle** (la bonne option depuis un portable) fournit un zip prêt à lancer sur votre poste (`chimerax isolde_session.py`
  ou `./run_coot.sh`) ; renvoyez le résultat avec **Upload model** ;
- enregistrez le modèle dans le dossier du job (ex. `save isolde_model.cif models #1`), puis **Finish** :
  le modèle devient la sortie du job et les jobs en attente (affinement final, validation…) démarrent.

### Avant le dépôt

- **Pre-deposition checks** : cohérence carte / demi-cartes / masque (boîte, voxel, origine), atomes
  dans la boîte, CC_mask, inclusion d'atomes au contour suggéré, identifiants de chaînes, résidus UNK,
  occupations nulles, B-factors non affinés ou de type pLDDT, chevauchements sévères, ruptures de chaîne,
  identité avec la séquence déposée.
- **Deposition package** : `model.cif` (entités configurées), `primary_map.mrc`, `half_map_1/2.mrc`,
  `mask.mrc`, `fsc.xml` (format EMDB), rapports de validation, `CHECKLIST.md` (valeurs à saisir dans
  OneDep : contour, résolution, taille de pixel, symétrie), `methods_draft.md` (texte + citations générés
  depuis l'historique des jobs) et `table1_draft.md`.
- **Sequence register check (checkMySequence)** : décalages de registre, chaînes sans séquence correspondante,
  différences avec la séquence attendue.
- **wwPDB validation report** : le rapport officiel (PDF + XML) calculé par le service de validation OneDep du
  wwPDB, avec les centiles ; le modèle et la carte primaire sont envoyés au serveur du wwPDB.

## 4. Lanes, GPU et cluster SLURM

```toml
[[lanes]]
name = "local"
type = "local"
max_jobs = 2
gpus = [0, 1]        # GPU distribués aux jobs (CUDA_VISIBLE_DEVICES) ; [] = non géré

[[lanes]]
name = "slurm-gpu"
type = "cluster"
max_jobs = 20
submit_cmd = "sbatch {script}"
status_cmd = "squeue -h -j {cluster_job_id} -o %T"
kill_cmd = "scancel {cluster_job_id}"
script_template = '''#!/bin/bash
#SBATCH --job-name=cryoplug_{project_uid}_{job_uid}
#SBATCH --output={job_dir}/cluster_stdout.log
#SBATCH --cpus-per-task={num_cpus}
#SBATCH --gres=gpu:{num_gpus}
{worker_cmd}
'''
```

Les workers communiquent uniquement par fichiers dans le dossier du job (`job.json`, `state.json`,
`job.log`, `report.json`) : il suffit que les dossiers de projet soient sur un système de fichiers
partagé ; aucune base de données ni port réseau n'est nécessaire sur les nœuds.

## 5. Comptes utilisateurs, accès réseau et sécurité

Par défaut le serveur n'écoute que sur `127.0.0.1` : l'interface ne s'ouvre que sur la machine elle-même.
Pour une plateforme ou un labo, la configuration conseillée est celle de CryoSPARC : **des comptes
personnels** et **le port ouvert** sur le réseau (idéalement en HTTPS).

### Comptes utilisateurs (comme CryoSPARC)

Dès qu'un compte existe, **tout le monde se connecte avec son nom d'utilisateur et son mot de passe**, sur
le serveur lui-même aussi. Tout se règle dans l'onglet **Settings** de l'interface :

- **Users** (administrateurs) : ajouter un utilisateur (mot de passe généré, affiché une seule fois, ou
  choisi ; nouveau mot de passe demandé à la première connexion), modifier son nom, son e-mail et son rôle,
  lui donner un nouveau mot de passe, le désactiver, le supprimer (ses projets vont à un autre compte ou aux
  seuls administrateurs). Pour chaque utilisateur, deux **chemins d'accès** :
  - son **dossier de projets**, où ses nouveaux projets sont créés (par défaut `<projects_root>/<nom>`) ;
  - les **dossiers qu'il peut lire** : données qu'il parcourt dans le navigateur de fichiers et donne aux
    jobs (projets CryoSPARC, cartes, modèles, bases de séquences). Tout chemin en dehors est refusé.
- **Access & security** (administrateurs) : mode de protection actif, adresse d'écoute, chiffrement,
  durée des sessions, longueur minimale des mots de passe.
- **My account** (chacun) : nom, e-mail, mot de passe, navigateurs connectés (et « déconnecter les autres »).

| **Rôles** | |
|---|---|
| **Administrateur** | Voit tous les projets, lit tous les dossiers (`browse_roots`), gère les comptes et les réglages. |
| **Utilisateur** | Voit ses projets et ceux partagés avec lui, crée ses projets dans son dossier, ne choisit des données que dans ses dossiers. La file d'attente lui montre l'occupation des lanes sans le détail des projets des autres. |

Chaque projet a un **propriétaire** ; il le **partage** depuis le menu **⋯** de la carte du projet (*Share…*) :
les membres y travaillent (jobs, fichiers, visualiseur 3D), mais les données qu'ils ajoutent doivent
toujours être dans *leurs* dossiers. Ce qui peut exécuter du code sur le serveur ou lire n'importe quel
dossier est réservé aux administrateurs : le job *Custom command*, les commandes ChimeraX supplémentaires
d'ISOLDE et les *Extra arguments* des programmes (un membre peut relancer un job préparé ainsi, sans en
changer ces réglages).

**Mise en place** (sans accès au terminal) :

1. Ouvrez **Settings › Users** sur le serveur (ou avec le jeton d'accès si le port est déjà ouvert) et créez
   le **compte administrateur** : vous restez connecté avec lui, et les projets existants deviennent les siens.
2. Ajoutez les utilisateurs, leur dossier de projets et les dossiers qu'ils peuvent lire.
3. Ouvrez le port (ci-dessous) : chacun se connecte depuis son poste, avec son compte.

| **Premier administrateur** (Settings › Users, avant tout compte) | **Access & security** : protection, écoute, sessions |
|---|---|
| ![](docs/images/accounts_first_admin.png) | ![](docs/images/accounts_access.png) |
| **Partager un projet** (menu ⋯ du projet) | **Navigateur de fichiers d'un utilisateur** : ses seuls dossiers |
| ![](docs/images/accounts_share.png) | ![](docs/images/accounts_browser.png) |
| **My account** (vue d'un utilisateur) | **Première connexion** avec un mot de passe temporaire |
| ![](docs/images/accounts_my_account.png) | ![](docs/images/accounts_first_login.png) |

En ligne de commande sur le serveur (par exemple pour un mot de passe administrateur oublié) :

```bash
cryoplug user add admin --admin                          # premier compte (administrateur)
cryoplug user add alice --generate --allow /data/alice   # mot de passe généré et affiché
cryoplug user set alice --projects-dir /data/alice/cryoplug --allow /data/alice --allow /data/shared
cryoplug user passwd admin                               # nouveau mot de passe (tapé deux fois)
cryoplug user disable bob | enable bob | delete bob --transfer-to alice
cryoplug user list
```

Comme dans CryoSPARC (où tout tourne sous le compte `cryosparc`), **les jobs s'exécutent sous le compte Unix
qui a lancé CryoPlug** : les dossiers de chaque utilisateur limitent ce qu'il peut choisir dans CryoPlug.
Pour une séparation stricte des données entre utilisateurs, appliquez aussi les permissions Unix.

### Ouvrir le port sur le réseau du labo

Dans `~/.cryoplug/config.toml` :

```toml
[server]
host = "0.0.0.0"
```

(ou ponctuellement `cryoplug start --host 0.0.0.0`), puis redémarrez CryoPlug
(`sudo systemctl restart cryoplug` avec le service). Avec des comptes, chacun ouvre
`http://<serveur>:39500` et se connecte avec son compte. Sans compte, le terminal affiche des liens avec un
**jeton d'accès** :

```
Open from any computer on the network:
  http://cryo-ws1:39500/?token=Xk3…
  http://192.168.1.42:39500/?token=Xk3…
Access token: Xk3…  (`cryoplug url` prints these links again)
```

Ce lien vous connecte (le navigateur le reste 30 jours) : c'est le moyen de créer le premier compte
administrateur depuis un portable. Dès qu'un compte existe, le jeton n'ouvre plus CryoPlug.

- **Mot de passe partagé** (sans comptes) : `password = "…"` dans `[server]` remplace le jeton. Changer le
  mot de passe déconnecte tous les navigateurs ; `cryoplug url --reset-token` (puis redémarrage) fait de
  même pour le jeton.
- **Avec le service systemd** : le lien est dans `journalctl -u cryoplug`, ou affiché par `cryoplug url`
  lancé par le même utilisateur que le service.
- **La page ne répond pas** : le pare-feu du serveur bloque probablement le port.

  ```bash
  sudo ufw allow 39500/tcp                                                          # Ubuntu / Debian
  sudo firewall-cmd --permanent --add-port=39500/tcp && sudo firewall-cmd --reload  # Rocky / RHEL
  ```

  Si le nom de la machine n'est pas connu du portable, utilisez l'adresse IP affichée.

### Tunnel SSH (hors du labo, ou sans toucher à la configuration)

Sur le portable :

```bash
ssh -N -L 39500:localhost:39500 utilisateur@serveur-cryoem
```

puis http://localhost:39500. Le trafic passe par SSH (chiffré) et rien n'est exposé sur le réseau.

### HTTPS (conseillé quand le port est ouvert)

En HTTP, les mots de passe, le jeton et les cookies de session circulent en clair sur le réseau local
(l'onglet *Access & security* le signale). Pour chiffrer avec un certificat auto-signé :

```bash
openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=$(hostname)" \
  -addext "subjectAltName=DNS:$(hostname)" -keyout ~/.cryoplug/key.pem -out ~/.cryoplug/cert.pem
```

```toml
[server]
ssl_certfile = "~/.cryoplug/cert.pem"
ssl_keyfile = "~/.cryoplug/key.pem"
```

L'adresse devient `https://…` ; le navigateur affiche un avertissement la première fois (certificat auto-signé).

### Ce qui est protégé

CryoPlug lance des programmes sur le serveur et en parcourt les fichiers. Dès qu'il écoute sur le réseau (ou
qu'un compte existe), toute requête doit donc être authentifiée :

- **Avec des comptes** : mots de passe hachés avec scrypt (jamais stockés en clair), sessions côté serveur
  (cookie aléatoire `HttpOnly`, `SameSite=Lax`, `Secure` en HTTPS), terminées à la déconnexion, au
  changement de mot de passe ou à la désactivation du compte. Après 5 mots de passe faux pour un compte depuis
  un même poste (20 tous comptes confondus), les connexions depuis ce poste sont suspendues 5 minutes. Les
  scripts s'authentifient avec `curl -u nom:motdepasse …`.
- **Sans compte** : cookie de session signé, ou `Authorization: Bearer <jeton>` pour les scripts
  (`curl -H "Authorization: Bearer $(cat ~/.cryoplug/access_token)" …`).
- Les requêtes qui modifient quelque chose doivent venir de la page CryoPlug elle-même (contrôle de
  l'origine), la page ne peut pas être intégrée dans un autre site, et en mode local sans connexion seuls les
  noms `localhost` / `127.0.0.1` sont acceptés : une page web malveillante ouverte dans le même navigateur ne
  peut pas piloter CryoPlug. Sur un poste partagé sans comptes, `auth = "always"` impose aussi le jeton en
  local.

## 6. Référence des jobs

La fiche de chaque job (rôle, cas d'usage, pièges, entrées, étapes suivantes) est dans
[docs/JOBS.md](docs/JOBS.md), générée depuis `cryoplug/jobhelp.py` par `cryoplug docs-jobs`.
`cryoplug jobtypes` liste les 52 types de jobs :

| Catégorie | Jobs |
|---|---|
| Import | Import from CryoSPARC (cartes et particules, coordonnées de 3D variability) · Import maps · Import atomic model (fichier / PDB / AlphaFold DB) · Import sequence (FASTA / UniProt, séparation protéine/ARN/ADN) · Import particles (`.cs` CryoSPARC, `.star` RELION) · Import volume series (3DVA, 3D Flex, cryoDRGN…) |
| Map processing | Half-map FSC · Directional resolution (3D FSC) · Local resolution (Phenix) · Create mask · Composite map (focused maps) · Map operations · LocScale 2 · EMmerNet · DeepEMhancer · EMReady · Anisotropy correction (spIsoNet) · Density modification (Phenix) · Auto-sharpen (Phenix) · Local anisotropic sharpening (Phenix) |
| Heterogeneity | cryoDRGN training · cryoDRGN analysis · cryoDRGN trajectory · Select particles (latent clusters) · Volume series analysis · Extract volume from series |
| Model building | ModelAngelo build · Identify chains (ModelAngelo HMM search) · CryoAtom2 build · AlphaFold2 (ColabFold) · Complex prediction (Boltz-2) · Process predicted model (Phenix) · Dock in map (Phenix) · Rigid-body fit (ChimeraX) · Ligand restraints (eLBOW) |
| Interactive | ISOLDE session · Coot session |
| Refinement | Real-space refinement (Phenix) · Refinement (Servalcat) · Add waters (phenix.douse) |
| Validation | Comprehensive validation (Phenix) · MolProbity · EMRinger · Map-model validation (Q-score, FSC) · Sequence register check (checkMySequence) · wwPDB validation report (OneDep) |
| Deposition | Pre-deposition checks · Deposition package |
| Utilities | Model operations · Merge models · Render images (ChimeraX) · Custom command |

Les métriques intégrées (FSC, résolution directionnelle, Q-score, FSC carte-modèle, CC, inclusion
d'atomes) sont calculées en NumPy. La résolution directionnelle utilise des FSC coniques (demi-angle 20°,
comme 3DFSC) sur un hémisphère de directions, lissées sur 3 coquilles ; le rapport pire/meilleure direction
signale une orientation préférentielle. Le Q-score suit la méthode de Pintilie et al. (2020) (σ = 0,6 Å, coquilles de 0 à 2 Å, points plus
proches de l'atome que de ses voisins) ; il est comparé à la valeur attendue à la résolution de la carte.

## 7. Architecture et ajout d'un nouveau logiciel

```
cryoplug/
  cli.py           commandes `cryoplug ...`
  config.py        configuration TOML (serveur, lanes, logiciels)
  server/app.py    API REST FastAPI + fichiers statiques de l'interface
  server/auth.py   connexion, sessions, jeton / mot de passe partagé, contrôles des requêtes
  server/accounts.py  API des comptes (Settings) et règles d'accès aux projets
  users.py         comptes : mots de passe (scrypt), sessions, limitation des tentatives, dossiers
  manager.py       projets, jobs, entrées/sorties, sessions interactives, workflows
  scheduler.py     lance les jobs prêts (dépendances, slots, GPU), suit les workers
  lanes.py         exécution locale ou soumission cluster
  worker.py        exécute un job dans son propre processus
  jobs/            un module par famille de jobs (imports, sharpening, building, ...)
  mrc.py           I/O MRC, FSC, filtres, cartes modèles
  analysis.py      Q-score, FSC carte-modèle, CC, inclusion d'atomes
  modelio.py       modèles et séquences (gemmi)
  workflows.py     modèles de pipelines
  web/             interface (HTML/CSS/JS sans étape de build)
```

Ajouter un logiciel = une classe :

```python
from cryoplug.jobs import register
from cryoplug.jobs.base import JobType, Param, Slot, OutputDef, extra_args_param

@register
class MyTool(JobType):
    name = "my_tool"
    title = "My tool"
    category = "Map processing"
    tool = "mytool"              # clé [tools.mytool] dans la config (déclarer aussi dans tools.TOOLS)
    gpu = 1
    inputs = [Slot("half_maps", ("half_maps",), "Half maps")]
    params = [Param("strength", "float", 1.0), extra_args_param()]
    outputs = [OutputDef("map", "map", "Enhanced map")]

    def run(self, ctx):
        hm = ctx.require("half_maps")
        out = ctx.path("enhanced.mrc")
        ctx.run([ctx.executable("mytool"), hm.files[0], hm.files[1], "-o", out,
                 "--res", ctx.resolution(), *ctx.split_extra()], tool="mytool")
        ctx.add_output("map", "map", out, inherit=["half_maps"])
```

L'interface (formulaire, entrées, glisser-déposer, graphe) est générée automatiquement à partir de ces
déclarations.

## 8. Tests

```bash
pip install ".[test]"
pytest
```

Les tests lancent de vrais workers sur des données synthétiques, avec de faux exécutables qui imitent les
interfaces de ModelAngelo, LocScale et Phenix : import CryoSPARC, FSC, chaîne complète jusqu'au paquet de
dépôt, CryoAtom2, recherche HMM, Boltz-2, spIsoNet, 3D FSC, eLBOW/douse, session ISOLDE, échec / arrêt de
jobs, workflows, API HTTP, concurrence API/planificateur, contrôle d'accès (connexion, jeton, mot de passe,
requêtes d'autres origines) et zone carte-modèle du visualiseur (comparée à un calcul exhaustif).
