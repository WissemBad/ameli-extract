# AmeliExtract — règles du projet

- Outil Python local pour les décomptes de paiement tiers Ameli en PDF texte.
- Ne jamais ajouter de PDF réel, de sortie JSON issue d'un vrai décompte, ni de donnée de santé au dépôt.
- Traiter l'identité de l'assurée et des bénéficiaires comme confidentielle : ne garder ni noms, ni NIR, ni adresse, ni coordonnées personnelles. La date de naissance est explicitement conservée.
- Garder les données de la CPAM et les données non identifiantes du document, dont prestations, soins, professionnels et règlements.
- Exécuter sans réseau. Ne jamais envoyer un document à un service d'OCR ou à une API externe.
- En cas de zone personnelle non reconnue, ne pas en copier le texte dans les logs ou messages d'erreur ; produire au besoin un statut `a_verifier`.
- Les exemples et tests emploient exclusivement des données fictives.
- Si les instructions évoluent, maintenir cette mémoire et le README avec les règles de confidentialité et la portée d'extraction.
