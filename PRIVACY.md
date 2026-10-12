# Politique de confidentialité locale

KPI Bridge est un outil **local**. Concrètement :

- **Aucun envoi à un tiers.** Les fichiers, extraits, demandes et résultats ne quittent pas l'ordinateur. Il n'y a
  ni télémétrie, ni statistiques d'usage, ni rapport d'erreur automatique, ni API cloud.
- **IA locale.** Les demandes sont interprétées par Ollama sur cette machine. Le modèle reçoit le schéma du
  tableau, des profils bornés et au plus trois courts échantillons par colonne texte, jamais le fichier entier.
- **Réseau.** Internet n'est utilisé qu'à l'installation (dépendances, Tesseract, Ollama, modèle) et pour une
  mise à jour demandée explicitement (`kpi update`). Le fonctionnement courant est hors ligne.
- **Stockage.** Les données restent dans `~/.kpi-bridge/data` (ou `KPI_DATA_DIR`) jusqu'à la suppression du
  projet. Elles ne sont pas chiffrées par l'application : la protection dépend aussi du chiffrement du disque.
- **Suppression.** « Supprimer le projet » retire les sources, les données dérivées, les résultats, les exports
  temporaires et les métadonnées. Ce n'est pas un effacement forensique ; les sauvegardes que vous avez faites
  (`kpi backup`) ou celles de votre système ne sont pas concernées.
- **Journaux.** Les journaux techniques (`data/logs`) ne contiennent ni le contenu des documents, ni les jetons,
  ni les cookies.
- **Démonstration.** Les fichiers d'exemple fournis sont entièrement synthétiques.
