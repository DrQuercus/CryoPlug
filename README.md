# CryoPlug

**Suite cryo-EM dans le navigateur, des films du microscope (ou de la meilleure carte CryoSPARC) jusqu'au dépôt PDB/EMDB.**

CryoPlug fait tout le traitement « single particle » avec **RELION 5** dans une interface à la CryoSPARC, ou prend
le relais une fois la reconstruction terminée dans CryoSPARC. Il pilote, depuis une interface web inspirée de
CryoSPARC (projets, cartes de jobs, file d'attente, lanes, **choix des GPU**, **suivi du matériel en direct**,
**comptes utilisateurs**), tous les logiciels installés sur votre serveur local :

| Étape | Logiciels pilotés | Jobs CryoPlug |
|---|---|---|
| Import | Films (TIFF, EER, MRC), CryoSPARC, RELION, fichiers MRC, PDB, AlphaFold DB, UniProt | `Import movies (RELION)`, `Import from CryoSPARC`, `Import maps`, `Import atomic model`, `Import sequence`, `Import particle coordinates`, `Import 2D templates` |
| Des films à la carte (RELION) | **RELION 5.1** : **prétraitement en direct** pendant la collecte (comme CryoSPARC Live), correction de mouvement (RELION ou MotionCor2), CTF (**CTFFIND** 4.1/5), **tri interactif des micrographies** (comme *Manually Curate Exposures*), picking **LoG** (avec un **tuner** de ses réglages), **détection du junk** (contamination, bords du film), par **modèles** ou **Topaz** (et son entraînement), **inspection des picks et picking manuel**, débruitage Topaz, extraction, **classification 2D** (VDAM/EM), **sélection des classes en cliquant dans la galerie** (2D et 3D, class ranker automatique ou **par corrélation avec une carte de référence**), modèle initial, classification 3D, **raffinement hétérogène**, **auto-refine** gold-standard, masques, post-traitement, **CTF refinement**, **polissage bayésien**, résolution locale, **multi-corps**, **DynaMight** ; groupes d'optique (beam shift EPU), **filaments** (options hélicoïdales), outils sur les particules (ensembles, rééquilibrage des vues, réduction, expansion de symétrie), **recherche du groupe ponctuel** | les commandes du GUI de RELION 5.1, fichiers STAR de RELION ; histogrammes à seuils déplaçables, micrographies avec CTF et dérive, particules sur les micrographies, galeries de classes, directions de vue et précision des poses ; workflow *Movies to map* |
| Tomographie (sous-tomogrammes) | **RELION 5** : import SerialEM (films et mdoc), correction de mouvement et CTF des séries d'inclinaison, **exclusion interactive des images**, alignement (**IMOD**, **AreTomo2**), reconstruction, débruitage **cryoCARE**, **pointage dans les tomogrammes** (particules, sphères, filaments) ou **template matching** (pytom-match-pick), extraction, moyenne, modèle initial, classification et raffinement gold-standard, CTF refinement, alignement des trames | `Import tilt series (RELION)`, `Exclude tilt images`, `Align tilt series (IMOD, AreTomo2)`, `Reconstruct tomograms (RELION)`, `Denoise tomograms (cryoCARE)`, `Pick particles in tomograms`, `Template matching (pytom-match-pick)`, `Extract subtomograms (RELION)`, `3D auto-refine (subtomograms)`… |
| Raffinement local (RELION) | **RELION 5** : raffinement local gold-standard (recherches locales depuis les poses CryoSPARC, Blush), **soustraction de signal** recentrée et recadrée (avec expansion de symétrie), **classification 3D focalisée** sans alignement, post-traitement, reconstruction | conversion CryoSPARC → RELION automatique (conventions de pyem), cartes toujours rendues sur la grille de la carte consensus, workflows *Focused refinement* et *Focused classification* |
| Amélioration de carte | **LocScale 2** (model-free / pseudo-modèle / model-based / hybride), **EMmerNet** (avec la carte de confiance pVDDT), **DeepEMhancer**, **EMReady2**, **spIsoNet** (correction de l'anisotropie due à l'orientation préférentielle), Phenix `resolve_cryo_em`, `map_sharpening` (Phenix 2), `auto_sharpen`, `local_aniso_sharpen`, **résolution locale** (`local_resolution`), **cartes composites** de raffinements locaux (`combine_focused_maps`) | + FSC demi-cartes (masquée, corrigée par randomisation de phase), **résolution directionnelle / 3D FSC**, **création de masques** (densité, chaînes du modèle, carte simulée du modèle comme `molmap`, densité proche du modèle ; dilatation et bord doux en Å), **masques dessinés à la main dans le visualiseur 3D** (comme Chimera : sphères, boîtes, cylindres, région de densité cliquée, zone autour de chaînes, carte simulée de chaînes) et opérations de carte (main, B-factor, filtre, masque, boîte) intégrées |
| Hétérogénéité (variabilité 3D) | **cryoDRGN** (contrôle des entrées, entraînement en deux tours, convergence, analyse, paysage, trajectoires), **cryoDRGN-AI** (ab initio, sans consensus : poses apprises, main et repère, validation), **CryoSPARC 3D Variability** et *3D Variability Display* / 3D Flex (import des coordonnées des particules et des séries de volumes) | **Explorateur de l'espace latent** interactif (UMAP / PCA, coloration par densité, défocus ou orientation, galerie des volumes avec alertes junk, lasso, images des particules, clusters → volumes en 3D, sélection de particules, trajectoire), **lecture des séries** dans le visualiseur 3D, **analyse de série** (carte de variabilité, corrélation entre états, chaînes qui bougent ou disparaissent), sélection de particules vers CryoSPARC ou un nouvel entraînement |
| Construction de modèle | **ModelAngelo** (avec ou sans séquence) et **identification de chaînes inconnues** (`hmm_search`), **CryoAtom2** (protéines, ARN/ADN, complexes, identification par base de séquences), ColabFold/AlphaFold2, **Boltz-2** (complexes protéines/acides nucléiques/ligands), Phenix `process_predicted_model`, `dock_in_map`, ChimeraX `fitmap` | + **placement de corps rigide intégré** (algorithme de `fitmap` : modèles, cartes et masques, à la main dans le visualiseur puis optimisé, ou recherche globale), restreintes de ligands (Phenix eLBOW), fusion et édition de modèles (gemmi) |
| Reconstruction interactive | **ISOLDE** (ChimeraX), **Coot** | sessions ouvertes en un clic sur l'écran du serveur, ou paquet téléchargeable pour votre poste |
| Affinement | Phenix `real_space_refine`, **Servalcat** (demi-cartes, cartes Fo-Fc), `phenix.douse` (eaux) | restreintes de ligands branchées directement sur l'affinement |
| Validation | MolProbity, EMRinger, Phenix `validation_cryoem`, **checkMySequence** (erreurs de registre de séquence), **rapport officiel wwPDB** (service de validation OneDep) | + **Q-score**, FSC carte-modèle, CC_mask, inclusion d'atomes intégrés (sans logiciel externe) |
| Dépôt | — | **Checklist pré-dépôt** automatique et **paquet OneDep** : mmCIF, cartes, FSC XML, niveau de contour recommandé, brouillon de *Méthodes* avec citations et de « Table 1 » |

> 🤖 **Avec une IA** : Claude Code, Codex, Claude Desktop ou un modèle local (LM Studio, Open WebUI…) pilotent
> CryoPlug par le protocole MCP, guidés par des skills cryo-EM — voir [§6](#6-piloter-cryoplug-avec-une-ia-mcp-et-skills).

![Jobs d'un projet](docs/images/jobs.png)

| Constructeur de job (pré-rempli, glisser-déposer) | Résolution directionnelle (3D FSC) |
|---|---|
| ![](docs/images/builder.png) | ![](docs/images/directional_fsc.png) |
| **Session ISOLDE prête** (ouvrir sur le serveur ou télécharger le paquet) | **Vue en arbre** (comme CryoSPARC) : cartes des jobs, lignes en couloirs colorées par type de données, lignée du job ouvert |
| ![](docs/images/isolde.png) | ![](docs/images/tree.png) |
| **Workflows** | **Checklist avant dépôt** |
| ![](docs/images/workflows.png) | ![](docs/images/checklist.png) |
| **Visualiseur 3D** : panneau de modèles, histogramme de seuil, masse enfermée, zone autour du modèle, détail et filtre, ligne de commande | **Coupes 2D en grand** à côté de la vue : zoom, valeur sous la souris, couleurs de la variabilité (thème sombre) |
| ![](docs/images/viewer.png) | ![](docs/images/viewer_slices.png) |
| **Coupe pleine** : avec le slab (ou en zoomant dans la carte), la densité coupée est dessinée pleine | **Surface du modèle** (ici *Cartoon + surface*, opacité réglable) |
| ![](docs/images/viewer_section.png) | ![](docs/images/viewer_surface.png) |
| **Lignée du job ouvert** : ses entrées en violet, les jobs qui l'utilisent en vert | **Mode sombre** |
| ![](docs/images/lineage.png) | ![](docs/images/dark.png) |
| **Éditeur de masque** dans le visualiseur 3D : sphère, région de densité cliquée… l'aperçu orange est calculé en direct | **Masque dessiné** : rapport du job (centre de masse, boîte pour recentrer les particules, coupes) |
| ![](docs/images/mask_editor.png) | ![](docs/images/mask_job.png) |

| Validation carte-modèle (Q-score, FSC) | Paquet de dépôt (méthodes, Table 1) | Boltz-2 (complexe + ligands) |
|---|---|---|
| ![](docs/images/validation.png) | ![](docs/images/package.png) | ![](docs/images/boltz.png) |

| **Résolution locale** dans le visualiseur (bleu = meilleure, échelle réglable) | **Création de masque** : densité proche des chaînes choisies, coupes de contrôle |
|---|---|
| ![](docs/images/viewer_locres.png) | ![](docs/images/mask.png) |
| **Résolution locale par chaîne et par résidu** | **Registre de séquence** (checkMySequence) |
| ![](docs/images/local_resolution.png) | ![](docs/images/checkmysequence.png) |

| **Explorateur de l'espace latent** (cryoDRGN) : clusters choisis → *Play in 3D*, *Keep…*, *Remove…*, *Trajectory…*, *Particle images* | **Série de volumes** jouée dans le visualiseur (barre de lecture : un bloc par volume chargé, même seuil pour tous, enregistrement vidéo) |
|---|---|
| ![](docs/images/latent_explorer.png) | ![](docs/images/viewer_series.png) |
| **Carte de variabilité** sur la carte moyenne (bleu = stable, rouge = variable ; échelle sur la vue) | **Analyse de série** : densité de chaque chaîne dans chaque volume (une hélice disparaît) |
| ![](docs/images/viewer_variability.png) | ![](docs/images/series_analysis.png) |

| **Entraînement cryoDRGN en direct** : époque, temps par époque, temps restant, courbes de perte redessinées à chaque époque | **Contrôle des entrées** : rétroprojection de 10 000 particules (FSC entre demi-lots, signe de la densité, verdict) |
|---|---|
| ![](docs/images/cryodrgn_live.png) | ![](docs/images/cryodrgn_input_check.png) |
| **Galerie des volumes des clusters** : trois projections, alertes sur le junk (*noisy*, *no fine detail*, *weak density*) | **Images des particules** des clusters suspects, depuis l'explorateur |
| ![](docs/images/cryodrgn_gallery.png) | ![](docs/images/cryodrgn_particles.png) |
| **Colorier l'espace latent** par le défocus (ou la densité, ‖z‖, l'orientation…) : ici les agrégats viennent de la glace épaisse | **Lasso** : garder, retirer ou générer le volume d'une région |
| ![](docs/images/cryodrgn_colour_defocus.png) | ![](docs/images/cryodrgn_lasso.png) |
| **L'espace latent est-il structural ?** Défocus, orientation et décalage prédits par l'espace latent, variance par composante, directions de vue | **Contrôle de convergence** : verdict et marche à suivre |
| ![](docs/images/cryodrgn_diagnostics.png) | ![](docs/images/cryodrgn_convergence.png) |
| **Volumes de particules représentatives au fil des époques** (corrélation entre époques, planche) | **Paysage des volumes** : états définis à partir de centaines de volumes générés |
| ![](docs/images/cryodrgn_convergence_volumes.png) | ![](docs/images/cryodrgn_landscape.png) |

| **cryoDRGN-AI en direct** (ab initio, sans consensus) : phase (recherche de poses ou SGD), temps restant, pertes par phase, stabilité des poses | **Comparaison avec les poses consensus** : main (ici le modèle était le miroir du consensus, corrigé), écart par particule |
|---|---|
| ![](docs/images/cryodrgn_ai_live.png) | ![](docs/images/cryodrgn_ai_comparison.png) |
| **Explorateur colorié par l'écart au consensus** : le junk (en haut) n'a pas de pose stable | **Reconstruction au fil des époques** et **validation** : rétroprojection avec les poses apprises |
| ![](docs/images/cryodrgn_ai_explorer.png) | ![](docs/images/cryodrgn_ai_validation.png) |

| **Comptes utilisateurs** (Settings › Users) : rôle, dossier des projets, dossiers lisibles, statut | **Ajout d'un utilisateur** : mot de passe généré ou choisi, dossiers autorisés |
|---|---|
| ![](docs/images/accounts_users.png) | ![](docs/images/accounts_add_user.png) |

| **Ressources en direct** (comme htop / nvtop) : CPU, mémoire, chaque GPU et ses processus, réseau, disques | **Choix des GPU** dans le constructeur : automatique, ou choisis sur leur occupation en direct |
|---|---|
| ![](docs/images/resources_live.png) | ![](docs/images/builder_gpus.png) |
| **Settings › Compute** : la machine et ses GPU, les lanes locales et SLURM | **Partitions SLURM** (onglet *Cluster* de la page Resources) |
| ![](docs/images/settings_compute.png) | ![](docs/images/resources_cluster.png) |

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
4. [Ressources : lanes, GPU, cluster SLURM et suivi en direct](#4-ressources--lanes-gpu-cluster-slurm-et-suivi-en-direct)
5. [Comptes utilisateurs, accès réseau et sécurité](#5-comptes-utilisateurs-accès-réseau-et-sécurité)
6. [Piloter CryoPlug avec une IA (MCP et skills)](#6-piloter-cryoplug-avec-une-ia-mcp-et-skills)
7. [Référence des jobs](#7-référence-des-jobs)
8. [Architecture et ajout d'un nouveau logiciel](#8-architecture-et-ajout-dun-nouveau-logiciel)
9. [Tests](#9-tests)

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
[tools.phenix]             # Phenix 2.x (ou 1.21) : le script phenix_env.sh du dossier d'installation
setup = "source /opt/phenix-2.2.1/phenix_env.sh"

[tools.relion]             # RELION 5.1 (tout le pipeline : relion_run_motioncorr, relion_refine_mpi, relion_postprocess…)
bin_dir = "/opt/relion-5.1/bin"
env = { MPIRUN = "mpirun" }  # lanceur MPI : "mpirun --oversubscribe" (Open MPI), "srun --mpi=pmix" (SLURM)…

[tools.ctffind]            # CTFFIND 4.1 ou 5, pour l'estimation de la CTF du pipeline RELION
executable = "/opt/ctffind-5.0/bin/ctffind5"

[tools.motioncor]          # facultatif : MotionCor2 / MotionCor3 sur GPU (la correction de mouvement de RELION n'en a pas besoin)
executable = "/opt/motioncor2/MotionCor2_1.6.4_Cuda121"

[tools.aretomo]            # tomographie : alignement des séries par AreTomo2 (GPU), lancé par relion_align_tiltseries
executable = "/opt/aretomo2/AreTomo2"

[tools.imod]               # tomographie : alignement IMOD (billes d'or ou suivi de patchs) par batchruntomo
bin_dir = "/usr/local/IMOD/bin"

[tools.cryocare]           # tomographie : débruitage cryoCARE (cryoCARE_train.py, cryoCARE_predict.py…)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate cryocare"

[tools.pytom]              # tomographie : template matching pytom-match-pick (GPU : pytom_match_template.py…)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate pytom_tm"

[tools.modelangelo]
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate model_angelo"

[tools.cryoatom]            # CryoAtom2 (installé par son install.sh dans l'env conda CryoAtom2)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate CryoAtom2"

[tools.locscale]            # LocScale 2 et EMmerNet
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate locscale"

[tools.spisonet]
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate spisonet"

[tools.boltz]               # Python 3.10-3.12 : pip install "boltz[cuda]" -U  (noyaux cuEquivariance des GPU récents)
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

[tools.colabfold]           # localcolabfold actuel (pixi) ; ancienne installation : .../colabfold-conda/bin
bin_dir = "/opt/localcolabfold/.pixi/envs/default/bin"

[tools.checkmysequence]     # conda env avec hmmer + checkMySequence (gitlab.com/gchojnowski/checkmysequence)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate checkmysequence"

[tools.onedep]              # client du serveur de validation wwPDB : pip install onedep_api (accès internet requis)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate onedep"

[tools.cryodrgn]            # cryoDRGN 3.x/4.x (cryoDRGN-AI : 4.2+, 4.3+ conseillé) : pip install cryodrgn (GPU)
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate cryodrgn"

[interactive]
display = ":0"   # écran (ou bureau TurboVNC, ex. ":1") où ouvrir Coot / ISOLDE depuis le navigateur
```

La page **Tools** de l'interface montre l'état de détection, le chemin et la version de chaque programme
(pour Phenix, la sortie de `phenix.version` ; si Phenix ne démarre pas dans l'environnement du service, la
raison s'affiche en orange). Quand un programme échoue, son explication (le « Sorry: … » de Phenix, la dernière
ligne d'une erreur Python…) est reprise dans l'erreur du job, sur sa carte.
Chaque job a aussi un champ *Extra arguments* (paramètres avancés) pour passer n'importe quelle option
supplémentaire, et le job **Custom command** permet d'intégrer un logiciel non prévu en quelques secondes.

Les fichiers de particules `.cs` des versions récentes de CryoSPARC (format compressé) se lisent avec
`cryosparc-tools` : `~/cryoplug-venv/bin/pip install cryosparc-tools` (inutile pour le format NumPy classique).

> Les lignes de commande suivent la documentation et le code source actuels de chaque logiciel
> (RELION 5.1 — tomographie comprise, d'après les sources de RELION 5.1.1 —, ModelAngelo 1.0.18, CryoAtom2 2.1, LocScale 2.4, spIsoNet 1.0, Boltz 2.2, ColabFold 1.6,
> DeepEMhancer 0.18, EMReady2, Servalcat 0.4, Phenix 2.x et 1.21, cryoDRGN 4.3, checkMySequence 1.5). Si votre version
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
4. **Workflows** crée une chaîne complète en un clic (des films à la carte avec RELION, de novo avec ModelAngelo ou
   CryoAtom2, modèle prédit AlphaFold, identification de protéines inconnues, comparaison des méthodes
   d'amélioration de carte, validation et dépôt), par exemple *De novo model → deposition (ModelAngelo)* :
   import → LocScale → ModelAngelo → real_space_refine → ISOLDE → affinement final → validation →
   checklist → paquet de dépôt. Les étapes optionnelles (ISOLDE, LocScale…) peuvent être retirées, les
   jobs en aval sont recâblés automatiquement.
5. La vue **Tree** dessine le pipeline de haut en bas, comme le *Tree View* de CryoSPARC (voir
   ci-dessous) ; chaque job a ses onglets *Overview* (métriques, graphes FSC / Q-score, sorties,
   paramètres, notes), *Log* (en direct) et *Files*. Quand un job est ouvert, ses **entrées** sont
   entourées en violet et les **jobs qui l'utilisent** en vert.
6. **View 3D** (dans les sorties d'un job, ou bouton **3D** au survol d'une carte de job) ouvre le
   visualiseur décrit ci-dessous.

### Importer depuis votre ordinateur

Les données n'ont pas besoin d'être déjà sur le serveur : le navigateur peut les y envoyer.

- **Sur un projet** : bouton **Upload** de la barre d'outils, ou **glissez des fichiers ou des dossiers n'importe où
  sur la page**. CryoPlug les envoie, **reconnaît ce qu'ils sont** d'après leur nom (noms CryoSPARC, RELION et
  EMDB) et propose les jobs d'import : demi-cartes, cartes non affinée et affinée, masque (*Import maps*), modèle
  PDB/mmCIF, séquence FASTA, dossier de job CryoSPARC (*Import from CryoSPARC*), série de volumes (dossier ou ZIP).
  Cochez, ouvrez au besoin un job dans le constructeur pour le modifier, puis *Create the jobs* ou *Create and
  queue*.
- **Dans le constructeur de job** : chaque champ de fichier a un bouton **Upload** à côté de *Browse*, et on peut y
  déposer un fichier ; un champ de dossier (le dossier du job CryoSPARC, par exemple) prend un dossier entier.
- **Dans le navigateur de fichiers** (*Browse*) : bouton **Upload from this computer** (on peut aussi déposer sur la
  liste), et le raccourci *Uploads of this project*.

![Import depuis l'ordinateur : les jobs proposés](docs/images/upload.png)

Les fichiers vont dans le dossier `uploads/` du projet. Ils partent par morceaux de 16 Mo, avec la progression, le
débit et le temps restant ; *Cancel* arrête l'envoi. Un transfert interrompu (réseau coupé, page fermée, serveur
redémarré) **reprend là où il s'était arrêté** quand on renvoie le même fichier. Un fichier déjà envoyé n'est pas
renvoyé ; un autre fichier du même nom en reçoit un nouveau (`map-2.mrc`). Un dossier garde ses noms et
sous-dossiers, sans les fichiers cachés (`.DS_Store`, fichiers `._*` de macOS). L'espace libre du serveur est
vérifié avant l'envoi. Les jobs d'import font un **lien physique** (*hard link*) vers les fichiers envoyés, sans
deuxième copie sur le disque. Avec des comptes, seuls le propriétaire du projet, ses membres et les administrateurs
peuvent y envoyer des fichiers.

En ligne de commande, depuis une autre machine où CryoPlug est installé : `cryoplug upload P1 carte.mrc
~/data/J245` (avec `--url` et `--token`, ou `CRYOPLUG_URL` et `CRYOPLUG_TOKEN`, comme pour les assistants IA,
section 6). La commande affiche les jobs d'import à créer.

### Actions, sélection multiple et raccourcis (comme CryoSPARC)

- Le bouton **⋯** d'une carte de job (ou un **clic droit**) ouvre son menu : ouvrir, *Continue with…*,
  voir en 3D, cloner, réinitialiser, copier le chemin du dossier, supprimer. Dans le détail d'un job,
  l'action principale (*Queue*, *Continue with…*, *Stop*…) reste en évidence et les autres sont dans le
  même menu **⋯**.
- En haut à droite, le **résumé** compte les jobs par statut (en cours, en file, en attente, échoués) :
  un clic sur un statut n'affiche que ces jobs, un second clic revient à tous.
- **Ctrl/⌘ + clic** ajoute un job à la sélection, **Maj + clic** une plage, **Ctrl/⌘ + A** tous les jobs
  affichés ; la barre de sélection propose **Queue**, **Stop**, **Clear**, **Clone**, **Workspaces…**,
  **Tags…** et **Delete** (avec le nombre de jobs concernés par chaque action et une confirmation). Cloner une
  sélection clone une chaîne : les copies sont reliées entre elles comme les originaux. **Compare** superpose les
  courbes FSC des jobs sélectionnés, aligne leurs résultats clés dans un tableau et ouvre leurs cartes ensemble en 3D.
- **Workspaces** (comme dans CryoSPARC) : le menu à gauche de la barre d'outils passe de *All jobs* à un groupe
  nommé de jobs (W1, W2…), en crée, le renomme ou le supprime ; un workspace ouvert ne montre que ses jobs
  (cartes, arbre, compteurs) et reçoit les jobs créés. Un job peut être dans plusieurs workspaces.
- **Étiquettes** : *Tags…* marque les jobs (« meilleure carte », « dataset 2 ») ; un clic sur une étiquette d'une
  carte filtre les jobs qui la portent.
- **Détacher / rattacher un projet** (comme CryoSPARC) : *Detach (keep the folder)* retire un projet en gardant son
  dossier, qui contient la fiche de chaque job (`cryoplug_job.json`) et ses workspaces ; *Attach a project folder*
  (ou `cryoplug project attach DOSSIER`) le rattache ici ou sur un autre serveur, aussi après la perte de la base.
- **Espace disque** : taille de chaque job (détail), du projet (en haut à droite, page des projets) ; *Clear
  intermediate results* / *Free space* supprime les itérations RELION intermédiaires (la dernière, les sorties et
  *Resume* restent) ; *Mark as complete* termine un run arrêté avec sa dernière itération.
- **N** nouveau job, **/** filtrer, **G** cartes ⇄ arbre, **Échap** vide la sélection ou ferme le détail,
  **?** liste des raccourcis.
- Le bouton de **tri** (à droite des filtres) change l'ordre des cartes : les plus récents ou les plus anciens
  d'abord, la dernière activité, par statut (en cours, en attente, en file, échoués…), par catégorie et type de job,
  par titre, ou par durée d'exécution. Le choix est gardé par le navigateur.

### Suivi en direct (tableau de bord)

Comme dans CryoSPARC, le panneau d'un job en cours se met à jour tout seul : tuiles des résultats clés,
avertissements du job, et **un curseur d'itération** sur les graphiques (FSC, coupes de la carte avec le contour
du masque, classes, part de chaque classe) des raffinements, classifications et modèles initiaux RELION ; **View 3D**
ouvre les cartes d'une itération pendant que le job continue. CryoAtom2 enhanced montre le modèle de chaque essai,
le tableau des essais, le meilleur modèle à ce stade avec ses scores, et sa carte des décisions qui grandit à chaque
essai. Détails : guide, *Suivre un job pendant qu'il tourne*.

### Vue en arbre

- Comme le *Tree View* de CryoSPARC : les cartes des jobs (vignette, statut, résultat ou progression) de haut
  en bas, les imports en haut, chaque job sous les jobs dont il prend les sorties ; un import utilisé tard
  (modèle, séquence) se place juste au-dessus de son premier usage. **Compact** : une ligne par job pour les
  gros projets.
- Lignes à angles droits dans des couloirs entre les rangées, un point à chaque raccord, colorées par ce
  qu'elles transportent (cartes, modèles et séquences, particules, autres) avec le détail de chaque lien en
  infobulle ; les liens d'un job vers les rangées du bas partagent un tronc qui se ramifie là où il sert.
- Un clic ouvre le job et allume sa lignée (entrées en violet, jobs qui l'utilisent en vert, ancêtres et
  descendants), le reste s'estompe (bouton **Lineage**) ; **Échap** ou double-clic sur le fond : désélection.
  *↑ upstream* / *↓ downstream* (en bas) n'affiche que l'amont ou l'aval du job ouvert, **−**/**+** pour le
  nombre de niveaux.
- La **molette** zoome sous le pointeur (comme CryoSPARC), glisser ou **Maj + molette** déplace ; en bas à gauche **F** (tout l'arbre),
  **−/+** et **0.25× / 0.5× / 1×** ; **T** centre sur la sélection ; mini-carte quand l'arbre dépasse la vue ;
  cadrage gardé par projet.
- Les filtres grisent les jobs écartés sans changer la forme de l'arbre. Pendant la construction d'un job,
  une carte glissée sur une entrée du constructeur branche la sortie qui convient.

### Visualiseur 3D

Une interface entre CryoSPARC et ChimeraX, dessinée par Mol\* (qui fonctionne hors-ligne après
`cryoplug fetch-viewer`) :

- **Panneau Models** à droite : chaque carte ou modèle porte un numéro (`#1`, `#2`…), un œil pour le
  masquer et une pastille de couleur. Pour une carte : **histogramme** des densités (échelle log) dont on
  fait glisser le trait pour régler le **seuil**, saisie en valeur absolue ou en **σ** avec le pourcentage
  de voxels au-dessus et la **masse de protéine enfermée** (saisir une masse fixe le seuil), style
  **Surface / Mesh / Transparent**, **Zone** : n'afficher la densité qu'à moins de *r* Å du modèle (comme
  `volume zone` de ChimeraX, calculée sur le serveur), **Detail** (finesse : de 128 voxels de côté au fichier
  d'origine, avec la taille du voxel et la résolution représentable) et **Filter** (passe-bas à 3–30 Å). Pour un
  modèle : **Cartoon**, **Cartoon + chaînes latérales**, **Sticks**, **Spheres**, **Surface** ou
  **Cartoon + surface** (opacité réglable), couleur par chaîne, structure secondaire, élément, B-factor,
  arc-en-ciel ou hydrophobicité, et « Go to » `A:45`.
- **Souris** : clic gauche-glisser tourne, clic droit (ou Ctrl + gauche) déplace, molette zoome,
  Maj + molette déplace les plans de coupe ; **cliquer sur un atome recentre la vue sur son résidu**
  (comme Coot), **Maj + clic sur deux atomes mesure leur distance**, et la barre d'état affiche l'atome
  survolé (`/A LYS 45 CA · B 32.1`).
- **Barre d'outils** : réinitialiser la vue, rotation continue, *slab* (tranche autour du centre, en % de
  l'épaisseur montrée ou en Å — **la densité coupée est dessinée pleine**, un ton plus sombre, comme quand on
  zoome dedans), projection orthographique, éclairage doux (occlusion ambiante), fond noir / gris / blanc,
  **coupes 2D** XY/XZ/YZ de la carte active (comme CryoSPARC : à côté de la vue, ou **en grand** dans une vue
  partagée avec zoom, déplacement, valeur sous la souris, seuil en teinte ou en contours, contraste, moyenne de
  plusieurs coupes, couleurs de la résolution locale ou de la variabilité), image PNG à 2× la taille de la
  fenêtre (avec l'échelle de couleur), **vidéo** (une lecture de série ou un tour complet, MP4/WebM) et
  **lien** vers la vue. **Open…** ajoute d'autres cartes, masques, demi-cartes ou modèles du projet.
- **Ligne de commande** façon ChimeraX (↑/↓ pour l'historique, `help` pour la liste) :
  `level 3σ`, `level 250kDa`, `style mesh`, `style #2 surface`, `color #2 bychain`, `view /A:45`, `zone 3`,
  `detail 300`, `lowpass 8`, `volume #1 level 0.5 style mesh`, `bg white`, `slab 8A`, `lighting soft`,
  `slices big`, `fullres #1`, `record spin`, `save figure.png scale 3`, `molmap #2/A,B 10` (masque), `move x 5 #2`,
  `turn y 30 #2`, `fitmap #2 inMap #1`…
- **Clavier** : **+ / −** seuil de la carte active (0,1 σ, Maj : 0,5 σ), **M** style suivant, **R** vue
  initiale, **S** rotation, **L** coupes, **:** ligne de commande, **?** aide.
- **Coloration par résolution locale** : menu *Colour* d'une carte (ou `color #1 localres 3 6`), échelle bleu
  (meilleure) → rouge (moins bonne) réglable avec sa légende ; le bouton *View 3D* d'un job *Local resolution* (ou
  d'un import CryoSPARC avec carte de résolution locale) ouvre directement la carte coloriée.
- **Coloration par variabilité** (sortie d'un job *Volume series analysis*) : même principe, en σ (bleu = stable, rouge =
  variable) ; `color #1 variability`.
- **Séries de volumes** (clusters et trajectoires cryoDRGN, frames de 3DVA) : *Play in 3D* charge les volumes (trois
  à la fois, celui qu'on vise avec le curseur d'abord) puis les joue comme un film — barre de lecture sur la vue
  (lecture/pause, précédent/suivant, curseur dont la piste montre les volumes chargés, vitesse, boucle ou
  aller-retour), **Espace**, **,** et **.**, commandes `play 10`, `stop`, `frame 5`, `vseries play #1`,
  `record series`. Le seuil est commun à tous les volumes.
- **Chargement** : les cartes arrivent en **aperçu binné** (au plus 200 voxels de côté, 80–128 pour les séries) et en
  16 bits — deux fois moins de données à transférer, mis en cache sur le serveur et revalidés par le navigateur ; une
  grande carte s'affiche d'abord en aperçu grossier puis s'affine. *Detail* (ou `fullres #1`) charge plus fin,
  jusqu'au fichier d'origine.
- **L'adresse de la page garde la vue** : cartes et modèles ouverts, réglages et caméra — recharger la page ou
  envoyer le lien (bouton *Link*) redonne la même vue.
- **Éditeur de masque** (bouton **Mask**, comme dans Chimera) : on choisit un outil — **sphère**, **boîte**,
  **cylindre** (deux clics), **région** (toute la densité connexe au niveau de contour, là où l'on clique),
  **zone** autour de chaînes d'un modèle ouvert, **carte simulée** de chaînes d'un modèle (*Model map*, le `molmap` de
  Chimera, aussi en commande : `molmap #2/A,B 10`), **densité** au-dessus du niveau — et un mode **ajouter**, **effacer** ou
  **garder seulement** ; un clic sur la carte place la forme (ou *At the centre of the view*, après avoir cliqué un atome).
  La liste des opérations s'annule (Ctrl + Z / Ctrl + Y), les curseurs modifient l'opération sélectionnée, et la
  dilatation et le bord doux se règlent en Å. Le serveur calcule le masque en direct sur une copie réduite de la carte
  (surface orange) ; **Save as job** lance le job *Mask drawn in the 3D viewer* qui le calcule sur la grille complète,
  avec son centre de masse et la boîte pour recentrer des particules. Le dessin reste dans l'adresse de la page et se
  rouvre depuis le job (*Mask editor*).
- **Move & fit** (bouton **Move**, comme dans Chimera) : cartes, masques et modèles se **déplacent dans l'espace** à la
  souris (glisser : rotation autour de l'objet, clic droit : translation, Maj + clic droit : profondeur ; Échap rend la
  souris à la caméra, Ctrl + Z annule) ou en commande (`move x 5 #2`, `turn y 30 #2`, `view initial`), puis
  s'**ajustent dans une carte** (rigid body fit, algorithme de `fitmap` de ChimeraX, calculé sur le serveur) : valeur
  moyenne de la carte aux atomes, ou overlap / corrélation de la carte simulée du modèle ou d'une carte, localement ou
  par recherche depuis des placements aléatoires (`fitmap #2 inMap #1 resolution 8 search 50`). **Save as job** écrit
  le modèle placé ou la carte rééchantillonnée sur la grille de la cible (job *Fit in map (rigid body)*).

### Des films à la carte (RELION)

Tout le traitement « single particle » se fait dans CryoPlug avec **RELION 5.1**, dans la même interface que le reste :
**Import movies (RELION)** (motif ou dossier des films, taille de pixel, dose, gain) → **Motion correction** →
**CTF estimation (CTFFIND)** → **Curate micrographs** → **Auto-picking** (Laplacien de Gaussienne, modèles 2D/3D ou
Topaz, avec son entraînement) → **Particle extraction** → **2D classification** → **Select 2D classes** →
**3D initial model** → **3D classification** → **3D auto-refine** → **Mask creation** → **RELION post-processing**,
puis **CTF refinement**, **Bayesian polishing** et un nouveau raffinement ; **Local resolution**, **3D multi-body
refinement** et **DynaMight** pour la suite. Le workflow *Movies to map (RELION single particle)* crée la chaîne.

- Chaque job lance la commande que construit l'interface de RELION 5.1 pour les mêmes réglages (`commands.sh` dans le
  dossier du job), depuis le dossier du projet et avec des chemins relatifs : les fichiers STAR sont ceux de RELION.
- **Comme CryoSPARC** : vignettes sur les cartes des jobs (micrographies, particules entourées, ajustements CTF,
  moyennes de classes), histogrammes avec les seuils de tri, trajectoires de dérive, et une **galerie de classes 2D**
  redessinée pendant la classification. *Select 2D classes* attend que vous cliquiez les bonnes classes (tri par
  particules ou résolution, Maj + clic pour une série) puis **Done** ; depuis une classification terminée, cliquer des
  classes puis **Select these classes** crée et lance la sélection. Sans clics : le class ranker de RELION, ou la
  **corrélation avec les projections d'une carte de référence** (comme *Reference Based Auto Select 2D*).
- **Symmetry search (RELION)** trouve le groupe ponctuel d'une carte C1 (comme le *Symmetry Search Utility*) : chaque
  groupe essayé (Cn, Dn, T, O, I) aligne la carte sur ses axes (relion_align_symmetry), puis la carte est comparée à
  sa copie symétrisée ; le plus grand groupe respecté est proposé, avec la carte alignée.
- **Outils interactifs** (le panneau du job s'élargit) : *Curate micrographs* attend avec un histogramme par mesure
  (ajustement CTF, défocus, astigmatisme, mouvement, glace…) dont on tire les limites, un nuage de points, le tableau
  de toutes les micrographies et chacune avec son ajustement CTF et sa dérive, acceptée ou rejetée au clavier (A / R) ;
  *Inspect picks / manual picking* montre les particules sur les micrographies (seuil de figure de mérite et gamme de
  power score sur leurs histogrammes, clic pour retirer ou ajouter, zoom, filtre passe-bas ; mode *Filaments* : début
  et fin de chaque tube pour l'extraction hélicale) ; *Curate particles (statistics)* trie les particules sur leurs
  statistiques (probabilité de la meilleure pose, log-vraisemblance, normalisation, défocus…) avec un histogramme par
  statistique, une carte de densité et des images des particules gardées et écartées. Le travail en cours est
  enregistré sur le serveur.
- **Live preprocessing** traite les films pendant la collecte (import, mouvement, CTF, tri, picking, extraction des
  seuls nouveaux films à chaque cycle) avec un tableau de bord en direct ; *Stop after this cycle* donne les sorties.
- **Diagnostics** à la CryoSPARC dans les raffinements et classifications : directions de vue (par classe aussi),
  précision des poses, décalages, part de chaque classe au fil des itérations ; galerie des classes 3D cliquable
  (*Select 3D classes*), *Heterogeneous refinement* avec des leurres.
- Logiciels : RELION, **CTFFIND** (4.1 ou 5) et, au besoin, **MotionCor2/3** dans *Tools* (§2) ; Topaz, le class
  ranker, Blush et DynaMight viennent de l'environnement Python installé avec RELION.

![Sélection des classes 2D](docs/images/relion_select2d.png)

![Tri interactif des micrographies](docs/images/relion_curate.png)

![Inspection des picks et picking manuel](docs/images/relion_pick_inspect.png)

![Tri des particules sur leurs statistiques](docs/images/relion_particle_curate.png)

![Prétraitement en direct pendant la collecte](docs/images/relion_live.png)

### Tomographie : des séries d'inclinaison à la moyenne de sous-tomogrammes (RELION 5)

La catégorie **Tomography** reprend la moyenne de sous-tomogrammes de **RELION 5** : **Import tilt series (RELION)**
(films et fichiers mdoc de SerialEM) → **Motion correction (tilt series)** (avec les sommes paires et impaires pour le
débruitage) → **CTF estimation (tilt series)** → **Exclude tilt images** → **Align tilt series (IMOD, AreTomo2)** →
**Reconstruct tomograms** → **Denoise tomograms (cryoCARE)** → **Pick particles in tomograms** (ou **Template matching
(pytom-match-pick)**, ou **Import tomogram coordinates**) → **Extract subtomograms** → **Reconstruct particle** → **3D initial model**, **3D classification** et
**3D auto-refine (subtomograms)** (`relion_refine --ios`) → masque et post-traitement, puis **CTF refinement
(tomography)** et **Frame alignment / polishing (tomography)**. Le workflow *Tilt series to subtomogram average
(RELION 5)* crée la chaîne ; *… picked by template matching* la fait tourner sans arrêt pour une structure connue.

- Les commandes sont celles de l'interface de RELION 5.1 ; les fichiers (jeux de séries, `tomograms.star`, jeux de
  particules et `optimisation_set.star`) sont ceux de RELION et se relisent dans RELION.
- **À la place des outils napari de RELION**, des outils dans le panneau du job : les images de chaque série dans
  l'ordre de l'angle avec leur ajustement de CTF (un clic pour exclure ou garder une image), une visionneuse des
  tomogrammes coupe par coupe (tranche de plusieurs coupes, filtre, zoom) et un **pointeur 3D** : particules isolées,
  sphères (vésicules, capsides : centre et rayon) ou filaments (points le long de chacun), écrits comme le pointeur de
  RELION avec les orientations et leurs a priori.
- **Template matching** (pytom-match-pick, GPU) pour une structure connue : le modèle mis à la taille de voxel et au
  contraste des tomogrammes, pondéré par les angles, défocus et doses des séries RELION, et les pics gardés selon le
  nombre de faux positifs admis ; un jeu de particules RELION 5 (positions, angles, scores) prêt pour l'extraction.
- Défauts de RELION 5.1.1 pris en charge : le préfixe de l'import (que `--prefix` rend inopérant) est ajouté après
  coup, un fichier mdoc sans images est refusé, le jeu de tomogrammes d'une reconstruction unique (`--tn`) est écrit,
  l'échelle par tomogramme de la CTF refinement passe par l'option que lit le programme (`--per_tomogram_scale`).
- Logiciels : RELION et CTFFIND, et selon les étapes **AreTomo2**, **IMOD** (`batchruntomo`), **cryoCARE** et
  **pytom-match-pick** dans *Tools* (§2).

| **Exclusion des images d'inclinaison** : images par angle avec leur ajustement de CTF, exclues d'un clic | **Pointage dans les tomogrammes** : sections des particules sur la coupe affichée |
|---|---|
| ![](docs/images/tomo_exclude.png) | ![](docs/images/tomo_picker.png) |

### Raffinement local et classification focalisée (RELION)

Pourquoi RELION en plus du *Local Refinement* de CryoSPARC : la **soustraction de signal** recentrée et recadrée,
la **classification 3D sans alignement** focalisée sur une région (l'outil de référence pour séparer les états d'un
domaine), l'**expansion de symétrie** (une copie d'un complexe symétrique devient une particule) et la régularisation
**Blush** de RELION 5, qui améliore nettement les petites régions flexibles. CryoPlug enchaîne tout à partir du
raffinement consensus de CryoSPARC, sans export manuel :

1. **Particules** : celles d'un import CryoSPARC (ou d'une sélection cryoDRGN) sont converties à la volée en fichier
   STAR RELION 5 avec les conventions de pyem (angles d'Euler de la rotation transposée, origines en Å, angles de CTF en
   degrés, groupes optiques = groupes d'exposition) — vérifiées numériquement contre pyem. Les **demi-jeux de CryoSPARC
   sont conservés** : la FSC reste gold-standard. Les piles d'images sont liées dans le dossier du job sous l'extension
   `.mrcs` que RELION exige ; RELION tourne depuis le dossier du projet, comme dans un projet RELION.
2. **RELION local refinement** sur tout le complexe (recherches locales dès le départ, 1,8°) : quelques itérations
   suffisent, et elles donnent le modèle RELION dont la soustraction a besoin.
3. **Masque de la région** : dessiné dans le visualiseur 3D (*Mask*), ou fait par *Create mask* (chaînes du modèle).
4. **RELION signal subtraction** : tout ce qui est hors du masque est retiré des images, les particules sont recentrées
   sur le centre de masse du masque (par voxels entiers) et mises dans une boîte plus petite (1,5 × l'étendue du masque).
5. **RELION local refinement** focalisé (Blush conseillé) ou **RELION 3D classification** sans alignement (T = 10–40
   pour une petite région) ; chaque classe donne une carte et ses particules.

Les cartes et les masques restent **toujours sur la grille de la carte consensus** : les jobs découpent la référence et le
masque pour RELION puis remettent les résultats en place exactement. Une carte focalisée se superpose donc au modèle et
va directement dans *Composite map*, *LocScale* ou la construction de modèle. Les workflows *Focused refinement with
signal subtraction (RELION)* et *Focused 3D classification (RELION)* créent toute la chaîne ; le job de masque attend
son dessin (bouton *Draw in 3D*) et les jobs suivants attendent le masque. Réglages MPI : au moins 3 processus pour un
raffinement (un maître + un par demi-jeu), 2 pour soustraire un raffinement ; le lanceur se règle par `MPIRUN` (§2).

### Hétérogénéité et variabilité 3D (cryoDRGN, CryoSPARC 3DVA)

CryoPlug suit le protocole des auteurs de cryoDRGN (Kinman et al. 2023, *Nature Protocols*) et ajoute à chaque étape
les contrôles qui disent si le résultat est fiable :

1. **Import from CryoSPARC** du raffinement consensus importe aussi ses **particules** (poses, CTF, emplacement des
   images ; les fichiers `particles` et `passthrough` sont fusionnés, les images restent dans le projet CryoSPARC).
2. **Contrôle des entrées** : les 10 000 premières particules sont rétroprojetées (FSC entre demi-lots, signe de la
   densité, verdict) avant d'engager des heures de GPU — automatiquement au début de l'entraînement, ou seul avec
   *cryoDRGN input check*.
3. **cryoDRGN training** : tour 1 à 128 px avec le petit réseau pour trouver le junk, tour 2 à 256 px avec le grand
   réseau sur les particules nettoyées (choix automatique). Courbes de perte **en direct**, temps restant,
   chargement paresseux automatique si la mémoire manque, version de cryoDRGN (3.x / 4.x) détectée.
4. Le rapport contient un **explorateur interactif** : particules en UMAP / PCA, coloriables par cluster, densité,
   ‖z‖, défocus, orientation ou décalage ; galerie des volumes des clusters avec alertes *weak / noisy / blurry* ;
   **images des particules** d'un cluster ; **lasso** pour garder, retirer ou générer le volume d'une région ; volume
   d'une particule ; outliers. Un tableau indique si l'espace latent code le défocus ou l'orientation (hétérogénéité
   non structurale), et une section *What to do next* donne la marche à suivre.
5. **cryoDRGN convergence check** (pertes, déplacement des particules, stabilité des voisinages, volumes de particules
   représentatives au fil des époques ; verdict et époque stable) et **cryoDRGN continue training** pour prolonger.
6. **Landscape analysis** (états définis à partir de centaines de volumes générés), **trajectoires** à travers les
   particules, **volumes à des particules** ou dans une région, **Volume series analysis** (carte de variabilité,
   corrélation entre volumes et, avec un modèle, chaînes qui bougent ou disparaissent).
7. **Select particles** (clusters, région du lasso ou outliers) écrit la sélection et les particules écartées (`.cs`
   pour CryoSPARC, `.star` pour RELION) et les indices pour un nouvel entraînement (images réduites réutilisées).
8. **cryoDRGN-AI ab initio reconstruction** (`cryodrgn abinit`, Levy et al. 2025) : poses et espace latent appris
   ensemble, **sans raffinement consensus** (particules d'un *Select 2D*, ou d'un raffinement dont les poses sont alors
   comparées). Suivi en direct par phase (pré-entraînement, recherche de poses, descente de gradient) avec temps
   restant, stabilité des poses entre points de sauvegarde, reconstruction au fil des époques ; **comparaison avec les
   poses consensus** (main — un ab initio peut sortir en miroir —, écart par particule, coloration de l'explorateur)
   ou alignement sur une carte de référence ; résultats ramenés dans le **repère et la main du consensus** ;
   rétroprojection de **validation** ; particules réécrites avec leurs **poses apprises** (`.cs` / `.star`) pour
   CryoSPARC ou un entraînement cryoDRGN. Analyse, continuation, convergence, paysage, trajectoires et sélection
   acceptent ces modèles (CryoPlug corrige ce que cryoDRGN fait différemment pour eux : translations en pixels,
   analyse dans le dossier d'entraînement, volumes du paysage inversés…).
9. Déjà calculé dans CryoSPARC : *Import from CryoSPARC* du job **3D Variability** (coordonnées des particules le long
   des composantes, mêmes explorateur et sélection) et **Import volume series** du job *3D Variability Display*
   (dossier, ZIP ou motif de fichiers ; une série par composante).

Workflows *Conformational heterogeneity (cryoDRGN)*, *Ab initio heterogeneity (cryoDRGN-AI)* et *3D variability
(CryoSPARC) → interpretation* ; tout le protocole, la lecture des rapports et les pièges dans le
[guide](cryoplug/web/docs/GUIDE.md) (chapitre 4 « Hétérogénéité avec cryoDRGN » dont §4.12 pour cryoDRGN-AI, §3.7,
recettes H et I).

### Construction automatique : CryoAtom2 enhanced

CryoAtom2 ne construit pas deux fois le même modèle : il parcourt les résidus dans un ordre aléatoire. Ses réglages
arbitrent aussi entre complétude et faux résidus, et la carte utilisée compte. Ses auteurs conseillent donc, sur
les cartes difficiles, de le lancer plusieurs fois et de garder le meilleur modèle. Ils conseillent aussi d'augmenter
les tours à basse résolution, de baisser les seuils Cα/P pour obtenir plus de résidus, de reconstruire avec les
séquences identifiées jusqu'à ce qu'il n'en apparaisse plus, et de retourner une carte dont les hélices sortent en
petits morceaux. **CryoAtom2 enhanced** fait tout cela seul :

1. il lance CryoAtom2 *N* fois par réglage. Le réglage *auto* essaie les réglages standard et, selon la résolution,
   **more residues** (seuils Cα/P abaissés à 0,5/0,4) ou **low resolution** (4 tours, chaque résidu vu 4 fois) ;
   *all* ajoute **strict** (seuils relevés). Il peut ensuite construire avec le meilleur réglage dans des copies de la
   carte **réaffûtées** (B = −30, +40… Å²) ou dans **d'autres cartes** branchées en entrée (LocScale, EMReady,
   modification de densité…) ;
2. chaque modèle est noté **contre la carte expérimentale**, même s'il a été construit dans une autre carte. Le score
   compte les **résidus ajustés**, ceux dont le Q-score moyen des atomes dépasse le Q attendu à la résolution moins 0,2.
   Une même densité tracée deux fois (Cα à moins de 2,5 Å) n'est comptée qu'une fois. Un résidu construit dans le
   bruit, ou avec la mauvaise chaîne latérale, a un Q-score bas : le score mesure à la fois la complétude et la
   justesse ;
3. le meilleur modèle est gardé. Ses **trous sont comblés** par les morceaux des autres essais qui s'ajustent là où
   il n'a rien : un fragment qui chevauche le modèle à ses bouts est rogné. La combinaison n'est retenue que si le
   score augmente ;
4. avec une **base de séquences** (protéines, acides nucléiques), les chaînes sont identifiées puis le modèle est
   reconstruit avec les séquences trouvées par au moins la moitié des essais, jusqu'à ce qu'aucune nouvelle
   n'apparaisse ;
5. si la plupart des résidus sont en morceaux de moins de 10 résidus, il construit une fois dans la carte **miroir**.
   Si elle fait nettement mieux, le job signale une main probablement inversée et donne la carte miroir et son modèle ;
6. **boucle carte–modèle** (*Improve the map and build again*), le cycle décrit par les auteurs de LocScale : améliorer
   la carte avec le modèle, construire, puis recommencer donne des cartes et des modèles meilleurs à chaque tour. Les
   résidus du meilleur modèle ajustés à la densité (B-factors remis à une valeur uniforme : CryoAtom2 y met une
   confiance) affûtent les **demi-cartes non filtrées** avec **LocScale hybride** (les zones sans modèle sont complétées
   par des pseudo-atomes, le mode recommandé pour un modèle partiel), **LocScale model-based** ou l'**affûtage par
   modèle de Phenix** (`phenix.map_sharpening` avec carte et modèle). CryoAtom2 reconstruit dans la nouvelle carte, et
   le cycle reprend avec le nouveau meilleur modèle tant que le score contre la carte **expérimentale** progresse (au
   plus *Map-model cycles*). On peut aussi cocher des méthodes sans modèle, qui donnent chacune une carte de plus à
   essayer : **EMmerNet**, **LocScale model-free**, **DeepEMhancer**, **EMReady2**, **modification de densité Phenix**.
   La carte du meilleur modèle est en sortie (*map_improved*). Un programme absent est sauté, avec un avertissement.

![CryoAtom2 enhanced : les essais comparés, dont deux cycles LocScale hybride](docs/images/cryoatom_enhanced.png)

La notation se fait toujours contre la carte expérimentale : une carte améliorée n'est jamais la référence, ce qui
évite qu'un modèle se « confirme » lui-même. LocScale model-based et hybride affinent les B-factors avec REFMAC5 (CCP4
doit être installé pour LocScale).

**Carte des décisions.** En tête du rapport, la *Decision map* montre le chemin suivi par le job, comme une carte
mentale : la carte de départ, les réglages choisis, les essais de chaque tour côte à côte dans une boîte, puis chaque
décision sous ce dont elle découle (meilleurs réglages, meilleur essai, trous comblés, séquences identifiées, carte
améliorée, main vérifiée). Le chemin qui mène au meilleur modèle est en bleu ; ce qui a été essayé sans être gardé est
estompé, les échecs en rouge. Un clic sur une tuile donne ses détails et ouvre sa carte et son modèle en 3D, et
éclaire le chemin qui y mène. La carte se remplit pendant le calcul (l'essai en cours clignote) et s'enregistre en
SVG ou PNG pour une présentation.

![CryoAtom2 enhanced : la carte des décisions, du réglage au meilleur modèle](docs/images/cryoatom_decision_map.png)

Le rapport compare tous les essais (*Runs compared* : résidus construits, ajustés, dupliqués, Q-score, CC, fragments,
score) et liste les morceaux ajoutés. Les modèles de chaque essai restent disponibles (*Models of every run*).
Comptez le nombre d'essais × la durée d'un CryoAtom2 (≈ 30 min pour 5 000 résidus sur une A100) : **Maximum runs**
plafonne le premier tour. Plusieurs GPU sont partagés par chaque essai, comme CryoAtom2 2.1 sait le faire.

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
  depuis l'historique des jobs) et `table1_draft.md`. Si le traitement RELION a été fait dans CryoPlug, chaque
  étape (films → carte, ou séries d'inclinaison → moyenne) est décrite avec ses réglages, ses résultats et les
  articles à citer, et le tableau reprend tension, exposition, défocus, pixel, particules initiales et finales,
  facteur B et plage de résolution locale.
- **Sequence register check (checkMySequence)** : décalages de registre, chaînes sans séquence correspondante,
  différences avec la séquence attendue.
- **wwPDB validation report** : le rapport officiel (PDF + XML) calculé par le service de validation OneDep du
  wwPDB, avec les centiles ; le modèle et la carte primaire sont envoyés au serveur du wwPDB.

## 4. Ressources : lanes, GPU, cluster SLURM et suivi en direct

Comme dans CryoSPARC, chaque job part sur une **lane** : la machine elle-même, dont les GPU sont distribués
aux jobs (un job par GPU, via `CUDA_VISIBLE_DEVICES`), ou un cluster SLURM. La première lane est celle par
défaut ; rien n'est à choisir pour lancer un job, mais tout peut l'être.

### Settings › Compute (administrateurs)

- **Matériel du serveur** : processeurs, mémoire et GPU détectés par `nvidia-smi`.
- **Lanes** : *Add a lane* (cette machine ou cluster SLURM), *Edit*, *Make default*, *Duplicate*, *Delete*.
  - Lane locale : les GPU qu'elle distribue (cases à cocher parmi les GPU détectés) et le nombre de jobs
    simultanés. Aucun GPU coché : la lane ne distribue pas de GPU (les programmes les voient tous).
  - Lane SLURM : partition par défaut et partitions proposées aux utilisateurs, compte (`--account`), QOS,
    durée et mémoire par job, ressource GPU (`--gres=gpu:<n>`, ou `gpu:a100`… ; vide : pas de `--gres`), options `#SBATCH`
    supplémentaires, lignes exécutées sur le nœud avant le job (`module load cuda/12.2`, conda…). Le script
    de soumission est écrit à partir de ces champs ; *Advanced* permet de changer les commandes
    (`sbatch`, `squeue`, `scancel`) ou de fournir son propre script (autres ordonnanceurs).
  - **Test** vérifie la lane depuis le serveur (`sinfo` et la liste des partitions, ou `nvidia-smi` pour les
    GPU) ; **Preview script** montre le script soumis pour un job à 1 GPU.
- Les lanes enregistrées ici remplacent les `[[lanes]]` de `config.toml` (*Use config.toml again* pour y
  revenir) et s'appliquent aux jobs lancés ensuite, sans redémarrer. Une lane qui a des jobs en file ou en
  cours ne peut être ni supprimée ni renommée ; un job préparé pour une lane supprimée part sur la lane par
  défaut.

### Section *Compute* du constructeur de job

- **Lane** : la lane par défaut est présélectionnée ; la liste indique ce que chacune offre.
- **GPU** : *Automatic* (au démarrage du job, CryoPlug prend le GPU libre dont la mémoire est la moins
  utilisée, programmes extérieurs compris) ou *Choose GPUs* : chaque GPU de la lane avec sa mémoire, sa
  charge, sa température et le job CryoPlug qui l'occupe. Un GPU occupé peut être choisi : le job attend
  qu'il se libère (« Waiting for GPU 1 (used by P2/J5) »). Le nombre de GPU suit le job (paramètre *GPUs*
  de cryoDRGN, *Needs a GPU* d'une commande personnalisée…).
- **CPU threads** : par défaut ceux du job ; sur la machine, fixe `OMP_NUM_THREADS` ; sous SLURM,
  `--cpus-per-task`. Pour les jobs Phenix, c'est leur paramètre *Processors* (le nombre de processus lancés)
  qui les fixe.
- **SLURM** : partition, durée et mémoire pour ce job (vides : réglages de la lane).
- Le panneau du job rappelle ces choix (section *Compute*) et les GPU attribués.

### Page *Resources*

- **Live usage**, comme htop et nvtop : CPU (total, chaque cœur, charge), mémoire (utilisée, cache, swap),
  chaque GPU (utilisation, mémoire, température, puissance, ventilateur, processus et job CryoPlug qui les a
  lancés), stockage (espace libre de chaque disque, celui des projets signalé ; un montage réseau qui ne
  répond plus est indiqué sans bloquer la page), débits réseau et disque, processus (tri par CPU, mémoire,
  mémoire GPU ; filtre *CryoPlug jobs only*). Graphes des 5 dernières minutes avec valeurs au survol ;
  mesures toutes les 2 s, seulement pendant que quelqu'un regarde la page. Les rafraîchissements gardent la
  position de la page ; une réponse manquée laisse les dernières valeurs sous un bandeau d'erreur.
- **Queue** : chaque lane (jobs en cours / en file, GPU occupés et par quel job) et la liste des jobs actifs,
  ceux en attente dans l'ordre où ils partiront (#1, #2… par lane). **To front** fait passer un job en tête de
  sa lane : les suivants le laissent passer tant qu'il attend un slot ou un GPU (passer devant les jobs des
  autres : administrateurs).
- **Notifications** : la cloche de la barre latérale annonce les jobs terminés, en échec, arrêtés ou qui
  attendent une action (notification du bureau si l'onglet est en arrière-plan — HTTPS ou `localhost` —,
  bandeau dans la page sinon, compteur dans le titre de l'onglet).
- **Cluster** (s'il y a une lane SLURM) : partitions (nœuds par état, CPU utilisés, GPU, mémoire par nœud,
  limite de temps), jobs en cours et en attente.
- Avec des comptes, tout le monde voit l'occupation de la machine, mais pas les commandes ni les jobs des
  autres utilisateurs ; les lanes et le matériel ne se règlent que par les administrateurs.

### Dans le fichier de configuration

Les lanes peuvent aussi être écrites dans `config.toml` (elles servent tant que rien n'est enregistré dans
*Settings › Compute*) :

```toml
[[lanes]]
name = "local"
type = "local"
max_jobs = 2
gpus = [0, 1]                     # GPU distribués aux jobs ; [] = non géré

[[lanes]]
name = "slurm-gpu"
type = "cluster"
max_jobs = 20
partition = "gpu"                 # --partition par défaut
partitions = ["gpu", "gpu-long"]  # proposées dans le constructeur de job
account = "lab"                   # --account
time_limit = "24:00:00"           # --time par défaut
mem = "64G"                       # --mem par défaut ("" = celle du cluster)
gres = "gpu"                      # les jobs GPU demandent --gres=gpu:<n>
extra_sbatch = "--constraint=a100"
setup = "module load cuda/12.2"   # exécuté sur le nœud avant le worker
# script_template = '''...'''     # script personnalisé : remplace le script écrit à partir des champs

[monitor]
nvidia_smi = "/usr/bin/nvidia-smi"  # si nvidia-smi n'est pas dans le PATH du serveur
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

### Se connecter avec une autre application du serveur (Open WebUI…)

Si le labo a déjà une application web avec des comptes — par exemple **Open WebUI**, l'interface d'un assistant IA
local —, CryoPlug peut accepter sa session : qui y est connecté ouvre le compte CryoPlug qui a la **même adresse
e-mail**, sans deuxième connexion. Le navigateur envoie le cookie de session de l'application à tous les ports de la
même machine : ouvrez CryoPlug avec le **même nom de serveur** que l'application (`http://<serveur>:39500` et
`http://<serveur>:8080`, pas l'un par son nom et l'autre par son adresse IP).

```toml
[external_login]
url = "http://localhost:8080/api/v1/auths/"   # « qui est connecté ? » d'Open WebUI, vu du serveur CryoPlug
name = "Open WebUI"                          # affiché sur la page de connexion
cookie = "token"                             # le cookie de session d'Open WebUI
login_url = "http://<serveur>:8080/auth"     # sa page de connexion, proposée par celle de CryoPlug
# match = "email"                            # ou "username"
# roles = ["user", "admin"]                  # un compte Open WebUI « en attente » est refusé
# create_accounts = false                    # true : compte CryoPlug (rôle user) créé à la première visite
```

- Il faut des **comptes CryoPlug** : l'application dit qui est la personne, son compte CryoPlug décide de ce
  qu'elle peut faire (un administrateur d'Open WebUI n'est pas administrateur de CryoPlug). L'adresse e-mail se
  règle dans *Settings › Users*.
- CryoPlug ne voit aucun mot de passe : il transmet la session reçue à l'adresse `url` seulement (directement, sans
  proxy ni redirection) et garde la réponse 60 s (`cache_seconds`) : se déconnecter de l'application ferme
  CryoPlug dans la minute. Un compte désactivé dans CryoPlug est refusé tout de suite.
- Se déconnecter de CryoPlug laisse la session de l'application ouverte : la page de connexion propose alors
  **Continue as …**, et dit pourquoi une session est refusée (compte en attente, aucun compte CryoPlug avec cette
  adresse).
- Les assistants qui transmettent la session de la personne (serveurs d'outils d'Open WebUI en authentification
  « session », §6) sont acceptés en `Authorization: Bearer`, avec les restrictions d'un jeton API.
- Avec une autre application : `url` répond 200 et un JSON qui contient l'adresse (`field = "email"`, ou un chemin
  comme `"user.email"`) pour une session valide, 401 sinon ; `roles = []` si elle n'a pas de rôles.

### Derrière un reverse proxy (nginx)

CryoPlug se sert à la racine d'un port, pas sous un sous-chemin, avec des comptes (ou `auth = "always"`). Le proxy
garde le nom et le port demandés (contrôle d'origine des requêtes qui modifient quelque chose) et CryoPlug lui fait
confiance pour l'adresse du client (limitation des essais de mot de passe) :

```toml
[server]
host = "127.0.0.1"                 # seul le proxy y accède
trusted_proxies = ["127.0.0.1"]    # l'adresse du proxy ("172.17.0.1" s'il tourne dans Docker sur la même machine)
public_url = "https://<serveur>"   # l'adresse que les gens ouvrent (liens donnés par les assistants)
```

[deploy/nginx-cryoplug.conf](deploy/nginx-cryoplug.conf) donne la configuration nginx (en-têtes, taille des
envois, délais). Si le proxy protège déjà la page avec le login d'une autre application (`auth_request`), ajoutez
`[external_login]` pour que CryoPlug sache *qui* est connecté, et laissez les assistants appeler CryoPlug sur son
propre port (`/mcp`, `/openapi`) plutôt qu'à travers le proxy.

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
- **Jetons API** (scripts, assistants IA) : propres à un compte, créés dans *Settings › AI agents* ou avec
  `cryoplug user token <nom>`, envoyés en `Authorization: Bearer`. Seule leur empreinte est stockée ; ils sont
  révoqués un par un, par un nouveau mot de passe ou par la désactivation du compte, et ne peuvent ni gérer les
  comptes, les jetons et les réglages, ni changer le mot de passe.
- Les requêtes qui modifient quelque chose doivent venir de la page CryoPlug elle-même (contrôle de
  l'origine), la page ne peut pas être intégrée dans un autre site, et en mode local sans connexion seuls les
  noms `localhost` / `127.0.0.1` sont acceptés : une page web malveillante ouverte dans le même navigateur ne
  peut pas piloter CryoPlug. Sur un poste partagé sans comptes, `auth = "always"` impose aussi le jeton en
  local.
- **Session d'une autre application** (`[external_login]`) : vérifiée auprès d'elle seule, jamais transmise
  ailleurs ; elle n'ouvre que le compte CryoPlug de même adresse e-mail, avec ses droits.

## 6. Piloter CryoPlug avec une IA (MCP et skills)

Un assistant IA — **Claude Code**, **Codex**, **Claude Desktop**, ou un modèle local dans **LM Studio**, **Open
WebUI**, **Goose**, **Jan**… — peut utiliser CryoPlug comme vous : lister les projets, choisir les jobs, les créer et
les mettre en file, suivre leur avancement, lire les rapports (chiffres, tableaux, courbes résumées avec leurs
seuils, images) et proposer la suite. Deux briques :

- **le serveur MCP** (*Model Context Protocol*, le standard ouvert qui relie les assistants à des outils) :
  `cryoplug mcp`, lancé par le client (stdio), ou l'adresse `http://<serveur>:39500/mcp` du serveur CryoPlug
  (*Streamable HTTP*), pour les clients qui se connectent à une URL. 32 outils, des ressources (le guide, les
  skills) et des prompts prêts à l'emploi ; les mêmes outils sont servis en **OpenAPI** à
  `http://<serveur>:39500/openapi` pour les assistants qui appellent des « serveurs d'outils » (Open WebUI) ;
- **les skills** (format ouvert *Agent Skills* : un dossier par skill avec son `SKILL.md`) : la méthode de travail et
  les règles de décision cryo-EM — quelle carte pour quoi, valeurs de référence de validation, protocoles cryoDRGN
  et RELION, dépannage.

![Settings › AI agents](docs/images/ai_agents.png)

### Brancher un assistant

Le plus simple : **Settings › AI agents** dans l'interface donne les commandes à copier (avec votre jeton), et
`cryoplug ai setup` sur le serveur les affiche. Par exemple :

```bash
# Claude Code, sur la machine du serveur
claude mcp add cryoplug -- cryoplug mcp
cryoplug ai skills --install claude      # skills dans ~/.claude/skills

# Codex
codex mcp add cryoplug -- cryoplug mcp
cryoplug ai skills --install agents      # ~/.agents/skills (Codex et autres clients du format Agent Skills)

# Vérifier la connexion comme le fera l'assistant
cryoplug ai check
```

Claude Desktop, LM Studio (*Program › Install › Edit mcp.json*), Cursor, Gemini CLI, Jan… prennent le même bloc
JSON (les applications de bureau ignorent le PATH du shell : mettez le chemin complet donné par `which cryoplug`) :

```json
{"mcpServers": {"cryoplug": {"command": "/chemin/vers/cryoplug", "args": ["mcp"],
                             "env": {"CRYOPLUG_TOKEN": "<jeton API>"}}}}
```

Open WebUI : *Settings › Admin › Integrations › External Tool Servers*, type **MCP (Streamable HTTP)**, URL
`http://<serveur>:39500/mcp`, authentification **Bearer** avec le jeton (si Open WebUI tourne dans Docker, l'adresse
doit être joignable depuis le conteneur), ou type **OpenAPI** (section suivante). Claude Code accepte aussi cette
adresse : `claude mcp add --transport http cryoplug http://<serveur>:39500/mcp --header "Authorization: Bearer <jeton>"`.

### Open WebUI et les serveurs d'outils OpenAPI

Open WebUI (comme d'autres assistants) appelle des outils décrits par une spécification **OpenAPI** : CryoPlug sert
les siens à `http://<serveur>:39500/openapi` (spécification : `/openapi/openapi.json`, publique car elle ne contient
aucune donnée). Chaque outil devient une fonction `cryoplug_<outil>` (`cryoplug_get_job`…), en **lecture seule** par
défaut : l'assistant consulte projets, jobs, résultats, journaux et images, sans rien lancer.

Dans Open WebUI : *Admin Settings › Integrations › Manage Tool Servers* (un serveur global, appelé par Open WebUI,
pas une connexion « directe » appelée par le navigateur de chacun), type **OpenAPI**, URL
`http://<serveur>:39500/openapi`, spécification `openapi.json`, et l'authentification :

| Authentification dans Open WebUI | Ce que voit l'assistant |
|---|---|
| **Bearer** avec un jeton API CryoPlug (*Settings › AI agents*) | les projets du compte de ce jeton, pour tout le monde : prenez un compte dédié, non administrateur, membre des projets à montrer |
| **Session** (la session de chaque personne), avec `[external_login]` (§5) | les projets de chaque personne, avec ses droits |

Donnez ensuite l'accès au serveur d'outils aux comptes voulus et ajoutez-le au modèle (ou préréglage) de
l'assistant. Vérifiez avec un compte Open WebUI **non administrateur** : un serveur d'outils sans droit d'accès est
retiré en silence pour les autres comptes, alors qu'il marche pour l'administrateur.

```toml
[server]
public_url = "http://<serveur>:39500"   # les réponses sur un projet ou un job finissent par le lien vers sa page

[openapi]
tools = "read"            # read (défaut), basic, all, ou une liste : ["server_status", "get_project", "get_job"]
language = "fr"           # descriptions en français, pour un assistant qui travaille en français
max_answer_chars = 6000   # au-delà, la réponse est coupée (le contexte des modèles locaux est petit)
```

- Les **descriptions**, que le modèle lit pour choisir un outil, existent en anglais et en français (une ou deux
  phrases, chaque argument avec un exemple). Les réponses sont le texte compact des outils ; au-delà de
  `max_answer_chars`, elles sont coupées avec les filtres qui permettent d'en demander moins. Les images (courbes
  FSC, vignettes des cartes) s'affichent dans la conversation.
- Les erreurs sont du JSON `{"error": "…"}` qui dit quoi corriger (« Project P9 not found (cryoplug_list_projects
  shows your projects) »), pour que le modèle se corrige au lieu de broder.
- Écrire (`tools = "basic"` ou `"all"`) reste limité par les droits du compte et par ce choix, jamais par une
  consigne au modèle ; `upload_file` n'est jamais offert ici.
- Open WebUI lit la spécification quand on enregistre le serveur : après un changement de `[openapi]` (et un
  redémarrage de CryoPlug), enregistrez-le de nouveau.
- Open WebUI dans Docker : l'adresse doit être joignable depuis le conteneur (sur la même machine,
  `http://172.17.0.1:39500/openapi`, CryoPlug écoutant avec `host = "0.0.0.0"`).
- Une phrase dans le prompt système de l'assistant l'aide à appeler les outils plutôt que de répondre de mémoire,
  par exemple : « Pour toute question sur les projets, jobs ou résultats cryo-EM de CryoPlug, appelle les outils
  cryoplug_… (commence par cryoplug_list_projects ou cryoplug_server_status) ; ne réponds jamais de mémoire. »

### Identité et droits de l'assistant

| Protection de CryoPlug | L'assistant utilise |
|---|---|
| Comptes utilisateurs | un **jeton API** de votre compte (*Settings › AI agents › Create a token*, ou `cryoplug user token <nom>`), passé en `CRYOPLUG_TOKEN` (ou en `Authorization: Bearer` pour `/mcp`) |
| Jeton d'accès | sur la machine du serveur, `cryoplug mcp` le lit seul dans `data_dir/access_token` (même compte Unix) ; ailleurs, `CRYOPLUG_TOKEN` = le jeton affiché par `cryoplug url` |
| Mot de passe partagé | lu dans la configuration de la machine ; ailleurs, `CRYOPLUG_TOKEN` = le mot de passe |
| Local, sans connexion | rien (localhost seulement) |

Depuis un autre ordinateur : installez-y CryoPlug (`pip install git+https://github.com/DrQuercus/CryoPlug.git`) et
ajoutez `CRYOPLUG_URL=http://<serveur>:39500` (ou ouvrez un tunnel SSH). Les identifiants locaux ne sont jamais
envoyés à une autre adresse que celle de la configuration.

L'assistant agit avec les droits de son jeton : mêmes projets et mêmes dossiers que le compte. Garde-fous :

- il ne peut **ni supprimer** de projet ou de job, **ni gérer** les comptes, les jetons et les réglages (le serveur
  le refuse aux jetons API), ni lancer de **commande arbitraire** (*Custom command*, *Extra arguments*, commandes
  ChimeraX), sauf `cryoplug mcp --allow-commands` pour un administrateur ;
- `kill_job` et `clear_job` (jobs en échec seulement) sont déclarés destructifs : les clients demandent confirmation ;
- les skills lui demandent de vous consulter avant les jobs GPU longs, les envois hors du serveur (rapport wwPDB,
  serveur MSA de ColabFold), et de ne jamais inventer un résultat.

Un jeton se révoque dans *Settings › AI agents* ou avec `cryoplug user token <nom> --revoke <id>`.

### Les outils

| Outils | Rôle |
|---|---|
| `server_status` | point de départ : compte, lanes et GPU, file d'attente, logiciels installés, projets, skills |
| `list_projects`, `get_project`, `create_project` | les projets ; un job par ligne (statut, entrées, sorties, résultats clés) |
| `list_job_types`, `describe_job_type` | ce que CryoPlug sait faire ; entrées, paramètres et défauts, conseils, jobs suivants |
| `find_outputs`, `create_job`, `update_job`, `queue_job`, `clone_job` | brancher les sorties (`"J3.map_sharp"`, ou `"J3"` : la bonne sortie est choisie), créer, modifier, mettre en file |
| `get_job`, `get_job_log`, `get_job_images`, `wait_for_jobs` | résultats (métriques, tableaux, courbes résumées et seuils franchis), journal, images, attente avec progression |
| `kill_job`, `clear_job` | arrêter un job ; remettre en préparation un job en échec |
| `prioritize_job` | faire passer un job en attente en tête de la file de sa lane |
| `control_job` | piloter un job « live » : arrêt après le cycle en cours (avec ses sorties), pause, reprise, nouvelles limites d'acceptation |
| `resume_job`, `mark_job_complete` | relancer une classification, un modèle initial ou un raffinement RELION en échec depuis sa dernière itération (Continue de RELION), ou le terminer avec les sorties de cette itération (Mark as complete de CryoSPARC) |
| `clone_jobs`, `create_workspace` | copier une chaîne de jobs (les copies reliées entre elles) ; ranger des jobs dans un workspace (`get_project workspace=W1`, étiquettes avec `update_job tags=`) |
| `list_workflows`, `apply_workflow` | les chaînes complètes prêtes à l'emploi |
| `browse_server_files`, `list_job_files`, `read_project_file`, `map_info` | trouver les données, lire les fichiers texte, en-tête d'une carte |
| `upload_file` | envoyer un fichier ou un dossier de l'ordinateur de l'assistant dans le projet (reprise, pas de doublon) ; répond le job d'import à créer |
| `list_skills`, `read_skill` | les skills, pour les clients qui ne les chargent pas eux-mêmes |

Sans client MCP, les mêmes outils en ligne de commande : `cryoplug ai call get_project project=P1`,
`cryoplug ai call create_job project=P1 type=map_fsc 'inputs={"half_maps": "J1"}' queue=true` ;
`cryoplug ai tools` les liste.

`upload_file` lit les fichiers de l'ordinateur où tourne `cryoplug mcp` : un assistant sur votre portable peut
envoyer vos cartes à un serveur distant. Il n'est pas offert par l'adresse `/mcp`, où « l'ordinateur de
l'assistant » serait le serveur lui-même. Sur la machine du serveur, l'assistant n'en a pas besoin : il donne
directement le chemin aux jobs d'import, s'il est dans les dossiers du compte (`browse_server_files`).

### Les skills

| Skill | Contenu |
|---|---|
| `cryoplug` | méthode de travail et règles, concepts, quelle carte pour quoi, syntaxe des jobs, workflows ; index des 132 types de jobs (généré) |
| `cryoplug-map-quality` | FSC, anisotropie, résolution locale, main ; LocScale, spIsoNet, modification de densité, cartes « réseau » |
| `cryoplug-masks-and-fitting` | masques (densité, atomes, molmap, densité proche du modèle), placement de corps rigide, cartes composites |
| `cryoplug-model-building` | stratégie selon la résolution, de novo, chaînes inconnues, modèles prédits, ligands |
| `cryoplug-refinement-validation` | affinement, ISOLDE/Coot, valeurs de référence et signaux d'alerte, dépôt |
| `cryoplug-heterogeneity` | 3DVA, protocole cryoDRGN, cryoDRGN-AI, sélection et validation des états |
| `cryoplug-relion-spa` | des films à la carte avec RELION : mouvement, CTF, picking, 2D/3D, raffinement, polissage |
| `cryoplug-relion-focused` | raffinement local et classification focalisée avec RELION |
| `cryoplug-troubleshooting` | diagnostiquer et corriger un job en échec ou bloqué |

Elles sont dans `cryoplug/skills/` ; le serveur MCP les offre aussi comme ressources (`cryoplug://skills/…`) et par
`read_skill`. Les prompts MCP (`review_project`, `assess_map`, `build_model`, `troubleshoot_job`,
`prepare_deposition`) apparaissent comme commandes dans les clients qui les gèrent (`/mcp__cryoplug__assess_map`
dans Claude Code).

### Avec un modèle local

Pour garder des données non publiées sur place : un modèle qui appelle les outils de façon fiable (par exemple
Qwen 3, Mistral Small, Llama ou gpt-oss), dans LM Studio, Open WebUI avec Ollama, Goose ou Jan. Les réponses des
outils sont du texte compact écrit pour des modèles modestes ; prévoyez un contexte d'au moins 32 000 tokens (la
description des 32 outils en occupe environ 7 000 ; celle des 17 outils de lecture, environ 2 500). Pour un petit modèle, `cryoplug mcp --tools basic` n'offre que
les 12 outils essentiels ; `--tools read` fait un assistant qui consulte sans rien changer (pour `/mcp` :
`http://<serveur>:39500/mcp?tools=basic`). Protocole : MCP 2025-11-25 et révisions antérieures ; les clients de la
révision 2026-07-28 reviennent automatiquement à 2025-11-25.

## 7. Référence des jobs

La fiche de chaque job (rôle, cas d'usage, pièges, entrées, étapes suivantes) est dans
[docs/JOBS.md](docs/JOBS.md), générée depuis `cryoplug/jobhelp.py` par `cryoplug docs-jobs`.
`cryoplug jobtypes` liste les 132 types de jobs :

| Catégorie | Jobs |
|---|---|
| Import | Import movies (RELION : films TIFF / EER / MRC et groupe optique) · Import from CryoSPARC (cartes et particules, coordonnées de 3D variability) · Import maps · Import atomic model (fichier / PDB / AlphaFold DB) · Import sequence (FASTA / UniProt, séparation protéine/ARN/ADN) · Import particles (`.cs` CryoSPARC, `.star` RELION) · Import particle coordinates (STAR, BOX, Topaz, CryoSPARC) · Import 2D templates · Import volume series (3DVA, 3D Flex, cryoDRGN…) |
| Preprocessing | Live preprocessing (pendant la collecte) · Estimate gain reference (RELION) · Motion correction (RELION ou MotionCor2) · CTF estimation (CTFFIND) · Curate micrographs (interactif ou seuils) · Denoise micrographs (Topaz) |
| Picking | Auto-picking (LoG, modèles 2D/3D, Topaz) · Tune LoG picking · Junk detector (micrographs) · Inspect picks / manual picking · Topaz training · Particle extraction (et ré-extraction) |
| Classification | 2D classification (VDAM / EM) · Select 2D classes (galerie cliquable, seuils, liste, class ranker ou carte de référence) · Amyloid initial model (RELION) · 3D classification · Heterogeneous refinement · Select 3D classes · Average power spectra (2D classes) |
| Reconstruction | 3D initial model · 3D auto-refine · RELION local refinement · RELION signal subtraction · RELION 3D classification (focused) · RELION post-processing · RELION reconstruction |
| Tomography | Import tilt series (RELION) · Motion correction et CTF estimation (tilt series) · Exclude tilt images · Align tilt series (IMOD, AreTomo2) · Reconstruct tomograms (RELION) · Denoise tomograms (cryoCARE) · Pick particles in tomograms · Template matching (pytom-match-pick) · Import tomogram coordinates · Extract subtomograms (RELION) · Reconstruct particle (RELION) · 3D initial model, 3D classification et 3D auto-refine (subtomograms) · CTF refinement (tomography) · Frame alignment / polishing (tomography) |
| Map processing | Half-map FSC · Directional resolution (3D FSC, cFAR, SCF) · Local resolution (Phenix) · Create mask · Segment map into regions · Mask creation (RELION) · Helical symmetry (RELION) · Align to symmetry axes (RELION) · Symmetry search (RELION) · Align 3D maps (et poses des particules) · Mask drawn in the 3D viewer · Composite map (focused maps) · Map operations (dont rééchantillonnage) · LocScale 2 · EMmerNet · DeepEMhancer · EMReady2 · Anisotropy correction (spIsoNet) · Density modification (Phenix) · Map sharpening (Phenix) · Auto-sharpen (Phenix) · Local anisotropic sharpening (Phenix) |
| Heterogeneity | 3D multi-body refinement (RELION) · DynaMight · cryoDRGN input check (backprojection) · cryoDRGN training · cryoDRGN-AI ab initio reconstruction · cryoDRGN continue training · cryoDRGN convergence check · cryoDRGN analysis · cryoDRGN landscape analysis · cryoDRGN trajectory · cryoDRGN volumes at particles · Select particles (latent space) · Volume series analysis · Extract volume from series |
| Model building | ModelAngelo build · Identify chains (ModelAngelo HMM search) · CryoAtom2 build · CryoAtom2 enhanced (meilleur de plusieurs essais) · AlphaFold2 (ColabFold) · Complex prediction (Boltz-2) · Process predicted model (Phenix) · Dock in map (Phenix) · Fit in map (rigid body) · Rigid-body fit (ChimeraX) · Ligand restraints (eLBOW) |
| Interactive | ISOLDE session · Coot session |
| Refinement | CTF refinement (RELION) · Bayesian polishing (RELION) · Real-space refinement (Phenix) · Refinement (Servalcat) · Add waters (phenix.douse) |
| Validation | Local resolution (RELION) · ResLog analysis (RELION) · Comprehensive validation (Phenix) · MolProbity · EMRinger · Map-model validation (Q-score, FSC) · Sequence register check (checkMySequence) · wwPDB validation report (OneDep) |
| Deposition | Pre-deposition checks · Deposition package |
| Utilities | Join star files (RELION) · Subset selection (RELION) · Particle sets (intersection, difference) · Curate particles (statistics) · Check particles (corrupt images) · Re-centre on a region · Simulate particles (RELION) · Restack particles · Rebalance orientations · Downsample particles · Symmetry expansion (RELION) · Optics groups (beam shift, file names) · Model operations · Merge models · Render images (ChimeraX) · Custom command |

Les métriques intégrées (FSC, résolution directionnelle, Q-score, FSC carte-modèle, CC, inclusion
d'atomes) sont calculées en NumPy. La résolution directionnelle utilise des FSC coniques (demi-angle 20°,
comme 3DFSC) sur un hémisphère de directions, lissées sur 3 coquilles ; le rapport pire/meilleure direction
signale une orientation préférentielle. Le Q-score suit la méthode de Pintilie et al. (2020) (σ = 0,6 Å, coquilles de 0 à 2 Å, points plus
proches de l'atome que de ses voisins) ; il est comparé à la valeur attendue à la résolution de la carte.

## 8. Architecture et ajout d'un nouveau logiciel

```
cryoplug/
  cli.py           commandes `cryoplug ...`
  config.py        configuration TOML (serveur, lanes, logiciels)
  server/app.py    API REST FastAPI + fichiers statiques de l'interface
  server/auth.py   connexion, sessions, jeton / mot de passe partagé, contrôles des requêtes
  server/accounts.py  API des comptes (Settings) et règles d'accès aux projets
  users.py         comptes : mots de passe (scrypt), sessions, limitation des tentatives, dossiers
  manager.py       projets, jobs, entrées/sorties, sessions interactives, workflows
  scheduler.py     lance les jobs prêts (dépendances, slots, GPU choisis ou les moins chargés), suit les workers
  lanes.py         exécution locale ou soumission cluster (script SLURM écrit à partir des réglages de la lane)
  monitor.py       utilisation en direct : CPU, mémoire, GPU (nvidia-smi), disques, réseau, processus
  worker.py        exécute un job dans son propre processus
  jobs/            un module par famille de jobs (imports, sharpening, building, ...)
  mrc.py           I/O MRC, FSC, filtres, cartes modèles
  analysis.py      Q-score, FSC carte-modèle, CC, inclusion d'atomes
  modelsearch.py   notation et fusion de modèles construits automatiquement (CryoAtom2 enhanced)
  modelio.py       modèles et séquences (gemmi)
  workflows.py     modèles de pipelines
  agent/           pour les assistants IA : client HTTP, outils, serveur MCP (stdio et /mcp), skills
  skills/          les skills (SKILL.md), installées par `cryoplug ai skills --install`
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
    gpu = 1                      # GPU par job (gpu_param = "gpus" : nombre donné par un paramètre)
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

## 9. Tests

```bash
pip install ".[test]"
pytest
```

Les tests lancent de vrais workers sur des données synthétiques, avec de faux exécutables qui imitent les
interfaces de ModelAngelo, LocScale et Phenix : import CryoSPARC, FSC, chaîne complète jusqu'au paquet de
dépôt, CryoAtom2, recherche HMM, Boltz-2, spIsoNet, 3D FSC, eLBOW/douse, comportements de Phenix 2 (paramètre
refusé et son message, programmes remplacés lancés avec `--force`, relance sur les contrôles carte/modèle),
session ISOLDE, échec / arrêt de jobs, workflows, API HTTP, concurrence API/planificateur, contrôle d'accès
(connexion, jeton, mot de passe, requêtes d'autres origines), zone carte-modèle du visualiseur (comparée à un
calcul exhaustif) et ressources (lanes modifiées en direct, GPU choisis ou automatiques, script SLURM,
moniteur avec un faux `nvidia-smi`, vue des partitions avec de faux `sinfo` / `squeue`, ce que voit chaque
utilisateur) et assistants IA (jetons API, outils de bout en bout, protocole MCP en stdio et en HTTP, format des
skills, index des jobs à jour).
