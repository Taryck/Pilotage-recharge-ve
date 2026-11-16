# Pilotage de recharge VE — EV Load Manager

Intégration personnalisée Home Assistant (HACS) pour répartir le courant disponible entre plusieurs bornes de recharge, sur une installation monophasée ou triphasée.

**Version 0.4.0 : calcul itératif complet avant la vague, consignes agrégées et journal détaillé.**

> Cette intégration vise à limiter les dépassements de courant mesurés, mais **ne remplace pas un disjoncteur, ni une protection électrique réglementaire ou un délesteur certifié**. En cas de panne, redémarrage ou indisponibilité de Home Assistant, aucune régulation n'est garantie.

## Installation HACS

1. HACS → Dépôts personnalisés → ajouter `https://github.com/Taryck/Pilotage-recharge-ve` de type **Intégration**.
2. Télécharger ou mettre à jour, puis redémarrer Home Assistant.
3. Paramètres → Appareils et services → **Gestion de recharge VE**.
4. Configurer l'installation, puis ajouter chaque borne comme sous-entrée.

Les configurations manuelles et les configurations importées depuis le simulateur sont indépendantes. Une configuration de test ne modifie pas les compteurs ou les bornes physiques.

## Configuration de l'installation

| Champ | Utilité |
|---|---|
| **Courant total L1, L2, L3** | Trois capteurs HA du courant total *réellement mesuré* sur chaque phase. Ils doivent inclure les bornes. Pour le parking simulé : trois capteurs virtuels du simulateur. |
| **Seuil maximal L1, L2, L3 (A)** | Courant autorisé par phase ; un dépassement est calculé indépendamment sur chaque phase : `max(0, courant_mesuré - seuil)`. |
| **Délai de confirmation de réduction (s)** | Délai maximal **sans progrès mesuré** pour une réduction. Défaut **6 s**, plage 1–120 s. La consigne HA ET la réduction physique totale doivent être confirmées, mais le délai est prolongé tant que le courant descend d'au moins 0,5 A entre observations. Une baisse de 8 A peut donc prendre plus de 6 s sans déclencher d'arrêt injustifié. Pendant ce temps, les autres bornes sont pilotées normalement. |
| **Intervalle de contrôle (s)** | Vérification périodique même sans nouveau changement d'état. Défaut **1 s**, plage 0,2–30 s. |
| **Attente après acquittement (s)** | Délai propre à chaque borne avant une **nouvelle modification de sa consigne**, laissant au courant physique le temps d'évoluer. Défaut **1 s**, plage 0–30 s. |

Il n'existe **plus de pause globale par ampère**. Le délai de confirmation est propre à chaque borne. Il n'y a **aucun nouvel essai** d'une consigne refusée ou non confirmée : une fois le délai expiré, le module envoie directement la commande d'arrêt de cette borne. La simple mise à jour de la consigne HA ne suffit plus à confirmer une baisse.

Les capteurs de courant acceptent les valeurs en A, mA et kA (converties en A). Une mesure configurée mais indisponible est considérée comme invalide et non comme un zéro.

## Configuration de chaque borne

| Champ | Utilité |
|---|---|
| **Nom** | Nom descriptif de la borne. |
| **Courant L1, L2, L3** | Capteurs mesurant le courant réellement tiré par la borne sur chaque phase. Une phase non utilisée peut être laissée non configurée (0 A), mais une mesure configurée devenue indisponible reste invalide. Les bornes triphasées fonctionnant temporairement en mono sont prises en compte par leurs mesures. |
| **Courant minimal / maximal (A)** | Valeurs de consigne permises par le régulateur. Doivent correspondre au matériel. Si une borne refuse une valeur, les tentatives et l'arrêt sont appliqués. |
| **Entité de consigne** | Entité modifiable `number` ou `input_number` recevant `set_value`, par pas de 1 A. |
| **Entité de commande** | Entité `select`, `input_select`, `switch`, `input_boolean`, `number` ou `input_number` commandant marche/arrêt. |
| **Valeur de démarrage / d'arrêt** | Valeurs exactes envoyées à l'entité de commande. Exemple LocalTuya : `select.marquise_borne_charging_command` avec les valeurs réellement proposées par la liste des options. Pour un switch : `on` / `off`. |

Attention : si le minimum déclaré dans l'outil est **inférieur** au vrai minimum matériel, la borne peut refuser une réduction et le module peut finir par lui envoyer l'arrêt.

## Logique du pilotage

### 1. Surveillance par phase

Sur événement de capteur ou à chaque intervalle de contrôle, on calcule les dépassements L1, L2 et L3. Si l'un des trois courants généraux est indisponible, aucune nouvelle décision de régulation fondée sur des mesures incomplètes n'est prise.

### 2. Calcul itératif et exécution d'une vague complète

**Le calcul s'effectue intégralement avant d'envoyer une commande.**

1. Calculer l'intensité à récupérer pour chacune des phases en surcharge.
2. Repérer les bornes qui consomment sur ces phases, en respectant leur intensité minimale configurable.
3. Choisir virtuellement la borne qui **consomme le plus** sur une phase en surcharge.
4. En cas d'**égalité d'intensité**, choisir la borne **la plus proche de sa consigne de départ mémorisée**, c'est-à-dire celle qui a été le moins réduite. Cette règle favorise une borne encore à 8 A (si son vrai minimum permet de la réduire) plutôt qu'une borne initialement à 32 A déjà descendue à 8 A.
5. Lui retirer **virtuellement 1 A**, et retirer le gain prévu sur toutes les phases sur lesquelles elle consomme (une borne triphasée libère jusqu'à 1 A sur chacune de ses trois phases).
6. **Reclasser toutes les bornes** en tenant compte des valeurs projetées, puis refaire un pas virtuel de 1 A jusqu'à compenser tous les dépassements possibles.
7. Regrouper les pas virtuels : on obtient **une seule consigne finale par borne**. Ce n'est qu'ensuite que toutes les commandes sont envoyées, sans attendre la réponse des autres bornes.
8. Contrôler les nouvelles consignes **et les courants réellement mesurés**. En cas de non-confirmation après le délai configuré **sans progrès**, envoyer directement **l'arrêt** à la borne, **sans réessai**. Une borne qui continue effectivement à diminuer son courant n'est pas arrêtée uniquement parce qu'une grosse réduction prend plus longtemps.
9. Recalculer les dépassements réels à la vague suivante ; une réduction encore en attente est prise en compte pour éviter de soustraire deux fois les mêmes ampères.

**Exemple : trois bornes monophasées sur L1 à 16, 12 et 10 A.**

- Pour **4 A** à récupérer, le calcul virtuel passe la première borne de 16 à 12 A : **une seule commande finale 16 → 12 A**, les autres bornes ne bougent pas.
- Pour **8 A** à récupérer, le reclassement et le départage donnent **16 → 10 A**, **12 → 10 A**, et **10 A inchangé**.
- Le classement ne répartit donc pas mécaniquement −1 A sur toutes les bornes.

Lorsqu'une borne approche de son minimum, elle cesse d'être éligible aux diminutions. Si aucun nouveau pas n'est possible et que le dépassement persiste, un arrêt peut être ordonné en dernier recours (après prise en compte des commandes déjà en cours).

**Mémorisation :** la consigne initiale, la commande initiale et les courants initiaux sont sauvegardés sur disque **avant toute première réduction effective** et conservés pendant les vagues successives.

### 3. Restauration progressive

En l'absence de dépassement, lorsque les phases utilisées par une borne disposent de **2 A de marge**, le contrôleur peut remonter sa consigne **de 1 A**. Ordre : **courant mesuré croissant**, puis **réduction cumulée décroissante**.

Les bornes **arrêtées par le module** sont considérées en priorité pour un redémarrage, sous réserve de la marge nécessaire pour leur consigne courante ; elles ne doivent pas rester indéfiniment en attente pendant que d'autres bornes remontent.

La restauration ne dépasse ni la consigne initiale mémorisée ni le nouveau maximum configuré. Le snapshot est supprimé lorsque la cible restaurable `min(consigne_initiale, nouveau_maximum)` est atteinte, même si le maximum a été réduit entre-temps.

### 4. Fin de recharge : suppression de la valeur initiale

**Dès qu'une borne reste indiquée comme active mais que ses trois phases mesurent 0 A, les anciennes valeurs mémorisées de charge sont effacées.** Il n'y a plus de recharge à restaurer. L'état absent d'une phase non configurée vaut 0 A ; une phase configurée mais indisponible ne déclenche pas cette suppression.

Exception temporaire : une borne que le module vient lui-même de redémarrer peut rester à 0 A pendant sa rampe physique de reprise ; une courte fenêtre de grâce évite d'effacer son état immédiatement. Une borne volontairement arrêtée et non arrêtée par le module perd également son snapshot.

### 5. Diagnostic et erreurs

Une exception dans le contrôleur est journalisée et fait apparaître une **notification persistante Home Assistant** ; les cycles suivants peuvent reprendre. Les pertes de mesures ou l'absence d'acquittement sont surveillées séparément par borne.

Le stockage `.storage/ev_load_manager_<entry_id>` est contrôlé à la lecture. Un contenu anormal empêche le chargement de l'intégration, afin de ne **pas perdre silencieusement** les consignes mémorisées.

Un capteur d'installation expose les courants, seuils et dépassements. Chaque borne dispose de capteurs d'état et de mémorisation. Les états techniques sont stables (`normal`, `overload`, `unavailable`, `monitoring`) et les libellés sont traduits en français/anglais.

## Utilisation avec Charging Station Simulator

1. Installer [Charging Station Simulator](https://github.com/Taryck/Charging-Station-Simulator).
2. Dans les options du simulateur, choisir **Synchroniser avec Pilotage recharge VE**.
3. Vérifier les trois capteurs de courant virtuels, les seuils et les bornes à ajouter.
4. Confirmer pour créer ou actualiser **uniquement l'installation de test**.

Les IDs des capteurs proviennent du registre HA (renommages acceptés) et les sous-entrées gardent un identifiant stable. Une deuxième synchronisation ne duplique pas les bornes. Les options ajustées manuellement dans le pilotage, notamment les délais et les seuils, **sont conservées** lors d'une resynchronisation : elles restent prioritaires sur les valeurs importées. Les bornes retirées du simulateur ne sont pas automatiquement supprimées.

## Journal et traçabilité

Le calcul et son exécution sont consultables dans **Journal de bord (Logbook)** de Home Assistant, par l'événement `ev_load_manager_audit` et dans les attributs du capteur **État de l'installation** (`recent_regulation_events` : les 20 derniers événements ; `pending_commands` : commandes en attente).

Les événements comprennent notamment :

- `overload` : valeurs totales, seuils et dépassements constatés par phase ;
- `calculation_start` : début de planification ;
- `calculation_step` : chaque pas virtuel de 1 A, la borne retenue, les phases et les valeurs avant/après ;
- `calculation_result` : consignes finales de la vague et dépassements résiduels prévisionnels ;
- `command` : ordre effectif envoyé à une borne ;
- `acknowledged` : confirmation du changement de consigne et, pour une baisse, de l'effet physique mesuré ;
- `ack_timeout` : absence de confirmation à l'expiration du délai, suivie d'un ordre d'arrêt ;
- `overload_cleared` : disparition du dépassement ;
- `error` : défaut interne ou échec de l'arrêt.

La durée de conservation des événements dans le Journal de bord dépend de la configuration du Recorder Home Assistant. Le capteur ne conserve que les 20 derniers événements en attributs ; les informations détaillées existent également dans le log de l'intégration au niveau INFO, lorsqu'il est activé.

## Tests et limites

Lancer `python -m pip install pytest && python -m pytest -q tests`. Le dépôt contient des tests du planner, de l'import du simulateur **et du contrôleur avec un faux runtime HA** (planification itérative, commandes concurrentes, acquittements, arrêt, snapshots, redémarrage, erreurs). Les tests sont aussi définis dans `.github/workflows/tests.yml`.

**Le chargement et le comportement de cette nouvelle version sur une instance Home Assistant réelle ne sont pas encore validés.** Faire d'abord un essai sur le parking simulé et vérifier les journaux, les courants et les commandes. Aucun test logiciel ne remplace la validation des protections électriques.
