"""French descriptions of the agent tools, for assistants that choose their tools from French descriptions (the
OpenAPI tool server with ``[openapi] language = "fr"``).  One or two sentences each — what the tool does, when to use
it, what it answers — and every argument with an example, as small local models need.  The answers of the tools
are not translated.

``FR[name] = (title, description, {argument: description})``; tests check that every tool and argument has one.
"""
from __future__ import annotations

PROJECT = "Identifiant du projet (P1) ou son titre, ex. « P3 » ou « Apoferritine »."
JOB = "Identifiant du job dans le projet, ex. « J12 »."

FR: dict[str, tuple[str, str, dict[str, str]]] = {
    "server_status": (
        "État de CryoPlug",
        "Point de départ : l'état du serveur CryoPlug (version, compte utilisé, files de calcul et GPU, jobs en cours "
        "ou en attente, logiciels installés, projets). À appeler quand on demande ce qui tourne ou si le serveur est "
        "occupé.",
        {}),
    "list_projects": (
        "Projets",
        "Les projets cryo-EM accessibles : identifiant (P1), titre, dossier, propriétaire, dernière mise à jour. À "
        "appeler pour retrouver un projet par son nom avant de regarder ses jobs.",
        {"include_archived": "Inclure aussi les projets archivés (false par défaut)."}),
    "create_project": (
        "Créer un projet",
        "Crée un projet (un par jeu de données ou par structure) : un dossier CP-<titre> dans le dossier des projets "
        "du compte, ou dans parent_dir. Les données s'y importent ensuite avec create_job.",
        {"title": "Titre du projet, ex. « Apoferritine 2026 ».",
         "description": "Description facultative.",
         "parent_dir": "Dossier du serveur où créer le dossier du projet (facultatif), ex. /data/equipe."}),
    "get_project": (
        "Projet et ses jobs",
        "Un projet et ses jobs, une ligne par job : identifiant (J3), statut, type, entrées, sorties, résultats clés "
        "(résolution…) et message ou erreur. À appeler pour savoir où en est un projet ; status et type n'en montrent "
        "qu'une partie.",
        {"project": PROJECT,
         "status": "Seulement les jobs de ces statuts, séparés par des virgules, ex. « failed » ou « running,queued ».",
         "type": "Seulement les types de job contenant ce texte, ex. « refine ».",
         "workspace": "Seulement les jobs de ce workspace, ex. « W2 ».",
         "tag": "Seulement les jobs portant cette étiquette, ex. « best ».",
         "limit": "Nombre maximal de jobs, les plus récents (60 par défaut)."}),
    "list_job_types": (
        "Types de jobs",
        "Ce que CryoPlug sait lancer, par catégorie (import, reconstruction RELION, traitement de carte, hétérogénéité, "
        "construction de modèle, affinement, validation, dépôt…), avec le logiciel requis et s'il est installé.",
        {"category": "Seulement cette catégorie (ou une partie de son nom), ex. « Validation ».",
         "search": "Seulement les types dont le nom, le titre ou la description contient ce texte, ex. « mask »."}),
    "describe_job_type": (
        "Décrire un type de job",
        "Tout sur un type de job : à quoi il sert, logiciel et GPU requis, entrées, sorties, chaque paramètre (défaut, "
        "choix, limites) et les conseils (quand l'employer, pièges, jobs suivants). À appeler pour expliquer un job ou "
        "ses paramètres.",
        {"name": "Nom du type de job ou son titre, ex. relion_refine3d ou locscale."}),
    "find_outputs": (
        "Sorties utilisables",
        "Les sorties des jobs d'un projet qui peuvent alimenter un nouveau job (cartes, demi-cartes, masques, "
        "particules, modèles…), terminées ou à venir.",
        {"project": PROJECT,
         "type": "Seulement ce type de donnée, ex. map, half_maps, mask, particles ou model.",
         "job": "Seulement les sorties de ce job, ex. « J12 »."}),
    "create_job": (
        "Créer un job",
        "Crée un job dans un projet, mis en file si queue=true. inputs relie ses entrées aux sorties d'autres jobs, "
        "params fixe des paramètres par leur nom (les autres gardent leur défaut). Vérifier d'abord le type avec "
        "describe_job_type.",
        {"project": PROJECT,
         "type": "Nom du type de job (list_job_types), ex. map_fsc.",
         "inputs": "Entrée → sortie d'un autre job, ex. {\"half_maps\": \"J1.half_maps\", \"mask\": \"J4\"}.",
         "params": "Nom du paramètre → valeur, ex. {\"resolution\": 3.2}.",
         "title": "Titre facultatif (par défaut, celui du type de job).",
         "queue": "Le mettre en file tout de suite (false par défaut).",
         "lane": "File de calcul facultative, ex. « local » (par défaut, celle par défaut).",
         "notes": "Notes facultatives gardées avec le job, ex. pourquoi il a été lancé.",
         "workspace": "Workspace facultatif où ranger le job, ex. « W2 ».",
         "tags": "Étiquettes facultatives, ex. [\"best\", \"dataset 2\"].",
         "resources": "Choix de calcul facultatifs : gpu_ids (liste), num_cpus, partition, time, mem (SLURM)."}),
    "update_job": (
        "Modifier un job avant son lancement",
        "Change les paramètres, les entrées, le titre, les notes, la file ou les ressources d'un job encore en "
        "préparation (un job en échec : clear_job d'abord). Seuls les paramètres et les entrées donnés changent.",
        {"project": PROJECT, "job": JOB,
         "params": "Nom du paramètre → nouvelle valeur, ex. {\"num_classes\": 4}.",
         "inputs": "Entrée → « J3.sortie », « J3 », ou null pour la déconnecter.",
         "title": "Nouveau titre.",
         "notes": "Nouvelles notes.",
         "lane": "Nouvelle file de calcul.",
         "tags": "Étiquettes du job, qui remplacent les actuelles ([] les retire).",
         "workspaces": "Workspaces du job, ex. [\"W1\"], qui remplacent les actuels.",
         "resources": "Choix de calcul : gpu_ids, num_cpus, partition, time, mem."}),
    "queue_job": (
        "Mettre un job en file",
        "Met en file un job en préparation : il démarre quand ses entrées sont prêtes et que sa file de calcul a de la "
        "place (et un GPU s'il en faut). Répond ce qui empêche la mise en file, s'il y a lieu.",
        {"project": PROJECT, "job": JOB,
         "lane": "File de calcul facultative (par défaut, celle du job ou celle par défaut)."}),
    "get_job": (
        "Résultats d'un job",
        "Un job en détail : statut et durée, entrées, paramètres modifiés, sorties, résultats clés et rapport "
        "(résolution FSC, métriques, tableaux, courbes résumées) ; pour un job en échec, l'erreur et la fin du journal. "
        "À appeler pour les résultats d'un job ou la cause d'un échec.",
        {"project": PROJECT, "job": JOB,
         "full_report": "Toutes les lignes des tableaux et les textes complets (false par défaut).",
         "all_params": "Tous les paramètres, pas seulement ceux modifiés (false par défaut)."}),
    "get_job_log": (
        "Journal d'un job",
        "Le journal d'un job (sortie du logiciel, messages de CryoPlug) : les dernières lignes, ou les lignes qui "
        "correspondent à un motif, avec du contexte. À appeler pour comprendre un échec ou suivre un job en cours.",
        {"project": PROJECT, "job": JOB,
         "tail": "Nombre de dernières lignes (120 par défaut, 2000 au plus).",
         "search": "Seulement les lignes qui correspondent à cette expression régulière, sans tenir compte de la "
                   "casse, ex. « error|warning ».",
         "context": "Lignes de contexte autour de chaque correspondance (2 par défaut)."}),
    "wait_for_jobs": (
        "Attendre la fin de jobs",
        "Attend que des jobs se terminent (au plus timeout_seconds), puis donne leur état et leurs résultats clés. Les "
        "jobs longs durent des heures : plutôt que d'attendre, le dire et revenir plus tard.",
        {"project": PROJECT,
         "jobs": "Identifiants des jobs, ex. [\"J3\", \"J4\"] (par défaut, les jobs actifs du projet).",
         "timeout_seconds": "Attente maximale en secondes (50 par défaut)."}),
    "kill_job": (
        "Arrêter un job",
        "Arrête un job en cours, ou retire un job de la file. Le travail déjà fait est perdu : demander d'abord "
        "confirmation à la personne.",
        {"project": PROJECT, "job": JOB}),
    "prioritize_job": (
        "Faire passer un job en tête de file",
        "Place un job en attente en tête de la file de sa lane : il part avant les autres jobs en attente. Passer "
        "devant les jobs d'autres personnes demande un administrateur.",
        {"project": PROJECT, "job": JOB}),
    "control_job": (
        "Piloter un job live",
        "Envoie une consigne à un job qui travaille par cycles (prétraitement live) : stop (finit avec ses sorties), "
        "pause, resume, ou thresholds (nouvelles limites d'acceptation). Demander d'abord confirmation : la collecte "
        "peut être en cours.",
        {"project": PROJECT, "job": JOB,
         "action": "stop, pause, resume ou thresholds.",
         "limits": "Pour thresholds, les nouvelles limites {métrique: [min, max]} (null : pas de borne), ex. "
                   "{\"ctf_res\": [null, 6], \"motion\": [null, 40]}."}),
    "clear_job": (
        "Remettre à zéro un job en échec",
        "Remet en préparation un job en échec ou arrêté, en effaçant ses fichiers, pour le corriger (update_job) et le "
        "relancer (queue_job). Un job terminé n'est pas effacé : clone_job en fait une copie.",
        {"project": PROJECT, "job": JOB}),
    "resume_job": (
        "Reprendre un calcul RELION",
        "Relance un raffinement 3D ou une classification 3D RELION en échec depuis sa dernière itération (Continue de "
        "RELION), sans refaire les heures déjà calculées. Vérifier d'abord la cause de l'échec avec get_job.",
        {"project": PROJECT, "job": JOB}),
    "mark_job_complete": (
        "Terminer avec la dernière itération",
        "Marque comme terminé un job RELION itératif en échec ou arrêté (classification 2D ou 3D, modèle initial, "
        "raffinement) avec les sorties de sa dernière itération, comme « Mark as complete » de CryoSPARC.",
        {"project": PROJECT, "job": JOB}),
    "clone_job": (
        "Copier un job",
        "Copie un job (même type, entrées et paramètres) en un nouveau job en préparation, en changeant éventuellement "
        "des paramètres, des entrées ou le titre : pour le relancer autrement en gardant l'original.",
        {"project": PROJECT, "job": JOB,
         "params": "Paramètres à changer dans la copie, ex. {\"resolution\": 3.0}.",
         "inputs": "Entrées à changer dans la copie, ex. {\"mask\": \"J8\"}.",
         "title": "Titre de la copie.",
         "queue": "Mettre la copie en file (false par défaut)."}),
    "clone_jobs": (
        "Copier une chaîne de jobs",
        "Copie plusieurs jobs ensemble : les entrées entre les jobs copiés relient les copies (une chaîne copiée reste "
        "une chaîne). Pour refaire une partie du traitement avec d'autres réglages.",
        {"project": PROJECT,
         "jobs": "Identifiants des jobs, ex. [\"J3\", \"J4\", \"J7\"].",
         "workspace": "Workspace facultatif pour les copies, ex. « W2 »."}),
    "create_workspace": (
        "Créer un workspace",
        "Crée un workspace dans un projet, comme dans CryoSPARC : un groupe nommé de jobs (un jeu de données, une "
        "étape, une question).",
        {"project": PROJECT,
         "title": "Titre, ex. « Nettoyage 2D ».",
         "description": "Description facultative.",
         "jobs": "Jobs à y mettre tout de suite, ex. [\"J3\", \"J4\"] (facultatif)."}),
    "list_workflows": (
        "Workflows",
        "Les chaînes de traitement prêtes à l'emploi (des films à la carte avec RELION, tomographie, carte CryoSPARC → "
        "modèle → affinement → validation → dépôt…) : leurs étapes et les paramètres demandés.",
        {}),
    "apply_workflow": (
        "Créer les jobs d'un workflow",
        "Crée dans un projet tous les jobs d'un workflow, reliés entre eux ; params donne les paramètres de chaque "
        "étape ; queue=true les met tous en file.",
        {"project": PROJECT,
         "workflow": "Identifiant du workflow (list_workflows), ex. relion_spa.",
         "params": "Identifiant d'étape → {paramètre: valeur}, ex. {\"import\": {\"job_dir\": \"/data/CS-x/J245\"}}.",
         "include": "Étapes facultatives à inclure, ex. [\"junk\"].",
         "queue": "Mettre tous les jobs en file (false par défaut).",
         "lane": "File de calcul facultative."}),
    "browse_server_files": (
        "Parcourir les dossiers du serveur",
        "Liste un dossier du serveur (dossiers, puis fichiers avec leur taille) pour trouver des données : dossiers de "
        "jobs CryoSPARC, films, demi-cartes, modèles, FASTA. Seulement les dossiers que le compte peut lire.",
        {"path": "Dossier du serveur, ex. /data/krios/2026-10 (par défaut, le premier dossier de données).",
         "pattern": "Seulement les noms qui correspondent à ce motif, ex. *half*.mrc.",
         "show_hidden": "Inclure les noms commençant par un point (false par défaut)."}),
    "upload_file": (
        "Envoyer un fichier de cet ordinateur",
        "Envoie un fichier ou un dossier de l'ordinateur où tourne l'assistant dans le dossier d'envoi d'un projet "
        "(par morceaux, avec reprise). Répond son chemin sur le serveur et le job d'import à créer.",
        {"project": PROJECT,
         "path": "Fichier ou dossier de cet ordinateur, chemin absolu ou ~/…, ex. ~/cartes/carte.mrc."}),
    "list_job_files": (
        "Fichiers d'un job",
        "Les fichiers du dossier d'un job (ou d'un sous-dossier), avec leur chemin relatif au projet, pour "
        "read_project_file.",
        {"project": PROJECT, "job": JOB,
         "subfolder": "Sous-dossier du dossier du job, ex. « Refine3D » (par défaut, le dossier lui-même)."}),
    "read_project_file": (
        "Lire un fichier texte d'un projet",
        "Lit un fichier texte d'un projet : journaux, tables STAR ou CSV, en-têtes PDB/mmCIF, FASTA, fichiers de "
        "paramètres. Les cartes et autres fichiers binaires sont refusés (map_info décrit une carte).",
        {"project": PROJECT,
         "path": "Chemin relatif au dossier du projet, ex. J3/run.out.",
         "start_line": "Première ligne à montrer, à partir de 1 (1 par défaut).",
         "max_lines": "Nombre de lignes (200 par défaut, 2000 au plus)."}),
    "get_job_images": (
        "Images d'un job",
        "Montre à la personne les images d'un job : figures de son rapport (courbes FSC, vues ChimeraX…) et vignettes "
        "de ses sorties (projections des cartes, traces Cα des modèles).",
        {"project": PROJECT, "job": JOB,
         "which": "Numéros des images, ex. « 1,3 », ou un mot de leur titre, ex. « FSC » (par défaut, jusqu'à 4)."}),
    "map_info": (
        "Décrire une carte",
        "L'en-tête d'une carte d'un projet : taille de boîte, taille de voxel, origine, taille du fichier.",
        {"project": PROJECT,
         "path": "Chemin de la carte relatif au projet, ou une sortie de job, ex. « J3.map_sharp »."}),
    "list_skills": (
        "Fiches méthode",
        "Les fiches méthode (skills) du travail cryo-EM avec CryoPlug : qualité de carte, masques, construction de "
        "modèle, affinement et validation, hétérogénéité, RELION, dépannage. Lire une fiche avec read_skill.",
        {}),
    "read_skill": (
        "Lire une fiche méthode",
        "Lit une fiche méthode (skill, en anglais) : procédures, règles de décision, valeurs de référence et pièges "
        "d'une tâche cryo-EM dans CryoPlug.",
        {"name": "Nom de la fiche, ex. cryoplug ou cryoplug-troubleshooting.",
         "file": "Fichier facultatif de la fiche, ex. references/job-types.md (SKILL.md par défaut)."}),
}
