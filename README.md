# CryoPlug

**Suite de post-traitement cryo-EM dans le navigateur, de la meilleure carte CryoSPARC jusqu'au dépôt PDB/EMDB.**

CryoPlug prend le relais une fois la reconstruction terminée dans CryoSPARC. Il pilote, depuis une
interface web inspirée de CryoSPARC (projets, cartes de jobs, file d'attente, lanes), tous les logiciels
installés sur votre serveur local :

| Étape | Logiciels pilotés | Jobs CryoPlug |
|---|---|---|
| Import | CryoSPARC, RELION, fichiers MRC, PDB, AlphaFold DB, UniProt | `Import from CryoSPARC`, `Import maps`, `Import atomic model`, `Import sequence` |
| Amélioration de carte | **LocScale 2** (model-free / pseudo-modèle / model-based / hybride), **EMmerNet**, **DeepEMhancer**, **EMReady**, Phenix `resolve_cryo_em`, `auto_sharpen`, `local_aniso_sharpen` | + FSC demi-cartes intégrée (masquée, corrigée par randomisation de phase) et opérations de carte (main, B-factor, filtre, masque, boîte) |
| Construction de modèle | **ModelAngelo** (avec ou sans séquence), ColabFold/AlphaFold2, Phenix `process_predicted_model`, `dock_in_map`, ChimeraX `fitmap` | + fusion et édition de modèles (gemmi) |
| Reconstruction interactive | **ISOLDE** (ChimeraX), **Coot** | sessions ouvertes en un clic sur l'écran du serveur, ou paquet téléchargeable pour votre poste |
| Affinement | Phenix `real_space_refine`, **Servalcat** (demi-cartes, cartes Fo-Fc) | |
| Validation | MolProbity, EMRinger, Phenix `validation_cryoem` | + **Q-score**, FSC carte-modèle, CC_mask, inclusion d'atomes intégrés (sans logiciel externe) |
| Dépôt | — | **Checklist pré-dépôt** automatique et **paquet OneDep** : mmCIF, cartes, FSC XML, niveau de contour recommandé, brouillon de *Méthodes* avec citations et de « Table 1 » |

![Jobs d'un projet](docs/images/jobs.png)

| Graphe du pipeline | Validation carte-modèle | Checklist avant dépôt |
|---|---|---|
| ![](docs/images/graph.png) | ![](docs/images/validation.png) | ![](docs/images/checklist.png) |

---

## Sommaire

1. [Installation sur le serveur](#1-installation-sur-le-serveur)
2. [Configurer les logiciels](#2-configurer-les-logiciels)
3. [Utilisation](#3-utilisation)
4. [Lanes, GPU et cluster SLURM](#4-lanes-gpu-et-cluster-slurm)
5. [Accès à distance et sécurité](#5-accès-à-distance-et-sécurité)
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

Ouvrez ensuite **http://localhost:39500**.

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

[tools.locscale]            # LocScale 2 et EMmerNet
setup = "source ~/miniconda3/etc/profile.d/conda.sh && conda activate locscale"

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

[interactive]
display = ":0"   # écran (ou bureau TurboVNC, ex. ":1") où ouvrir Coot / ISOLDE depuis le navigateur
```

La page **Tools** de l'interface montre l'état de détection, le chemin et la version de chaque programme.
Chaque job a aussi un champ *Extra arguments* (paramètres avancés) pour passer n'importe quelle option
supplémentaire, et le job **Custom command** permet d'intégrer un logiciel non prévu en quelques secondes.

> Les lignes de commande suivent la documentation actuelle de chaque logiciel (ModelAngelo 1.x,
> LocScale 2, DeepEMhancer, EMReady, Servalcat `refine_spa_norefmac`, Phenix 1.21). Si votre version
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
   de jobs : glissez-les sur les entrées.
4. **Workflows** crée une chaîne complète en un clic, par exemple
   *De novo model → deposition (ModelAngelo)* :
   import → LocScale → ModelAngelo → real_space_refine → ISOLDE → affinement final → validation →
   checklist → paquet de dépôt. Les étapes optionnelles (ISOLDE, LocScale…) peuvent être retirées, les
   jobs en aval sont recâblés automatiquement.
5. La vue **Graph** montre le pipeline ; chaque job a ses onglets *Overview* (métriques, graphes FSC /
   Q-score, sorties, paramètres, notes), *Log* (en direct) et *Files*.
6. **View 3D** ouvre les cartes (aperçu sous-échantillonné, ou pleine résolution) et modèles dans Mol*.

### Sessions interactives ISOLDE / Coot

Le job prépare la session (modèle + cartes associées, `clipper associate`, `isolde start`) puis passe en
`waiting` :

- **Open on server** ouvre ChimeraX/ISOLDE ou Coot sur l'affichage configuré (`[interactive] display`) ;
- **Download session bundle** fournit un zip prêt à lancer sur votre poste (`chimerax isolde_session.py`
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
- Dernière étape recommandée : le serveur de validation officiel wwPDB (https://validate.wwpdb.org).

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

## 5. Accès à distance et sécurité

Par défaut le serveur n'écoute que sur `127.0.0.1`. Depuis votre portable :

```bash
ssh -N -L 39500:localhost:39500 utilisateur@serveur-cryoem
```

puis http://localhost:39500. Pour l'ouvrir au réseau du labo, mettez `host = "0.0.0.0"` **et** un mot de
passe (`[server] password`, authentification HTTP basique). Le navigateur de fichiers est limité à
`browse_roots`.

## 6. Référence des jobs

`cryoplug jobtypes` liste les 32 types de jobs :

| Catégorie | Jobs |
|---|---|
| Import | Import from CryoSPARC · Import maps · Import atomic model (fichier / PDB / AlphaFold DB) · Import sequence (FASTA / UniProt, séparation protéine/ARN/ADN) |
| Map processing | Half-map FSC · Map operations · LocScale 2 · EMmerNet · DeepEMhancer · EMReady · Density modification (Phenix) · Auto-sharpen (Phenix) · Local anisotropic sharpening (Phenix) |
| Model building | ModelAngelo build · AlphaFold2 (ColabFold) · Process predicted model (Phenix) · Dock in map (Phenix) · Rigid-body fit (ChimeraX) |
| Interactive | ISOLDE session · Coot session |
| Refinement | Real-space refinement (Phenix) · Refinement (Servalcat) |
| Validation | Comprehensive validation (Phenix) · MolProbity · EMRinger · Map-model validation (Q-score, FSC) |
| Deposition | Pre-deposition checks · Deposition package |
| Utilities | Model operations · Merge models · Render images (ChimeraX) · Custom command |

Les métriques intégrées (FSC, Q-score, FSC carte-modèle, CC, inclusion d'atomes) sont calculées en
NumPy. Le Q-score suit la méthode de Pintilie et al. (2020) (σ = 0,6 Å, coquilles de 0 à 2 Å, points plus
proches de l'atome que de ses voisins) ; il est comparé à la valeur attendue à la résolution de la carte.

## 7. Architecture et ajout d'un nouveau logiciel

```
cryoplug/
  cli.py           commandes `cryoplug ...`
  config.py        configuration TOML (serveur, lanes, logiciels)
  server/app.py    API REST FastAPI + fichiers statiques de l'interface
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
dépôt, session ISOLDE, échec / arrêt de jobs, workflows, API HTTP et concurrence API/planificateur.
