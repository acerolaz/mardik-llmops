"""Proxy de dérive — [FOURNI]. Placé entre l'application et le fournisseur LLM.

Le modèle reste vrai ; la panne est commandée. Selon ``DRIFT`` :

* ``off``      transparent (défaut) ;
* ``latence``  chaque appel dure ×4 (on attend 3 fois la durée réelle en plus) ;
* ``erreurs``  10 % des appels renvoient HTTP 502 ;
* ``score``    les scores de confiance renvoyés par le modèle sont dégradés
                vers ~0,5 (le JSON est réécrit à la volée) — la sortie reste
                valide, mais « le modèle n'est plus sûr de lui ».

Le mode se change à chaud, sans redémarrage :

    GET  /_drift            → {"mode": "off"}
    POST /_drift {"mode": "score"}
    POST /_drift {"mode": "off"}

C'est ce que pilote ``scripts/traffic_sim.py --mode derive-score``.

Routage amont : l'en-tête ``x-mardik-provider`` (posé par ``app/llm_client.py``)
choisit l'amont — ``azure`` → ``AZURE_AI_ENDPOINT``,
``ollama`` → ``OLLAMA_URL`` (défaut http://localhost:11434).

    python -m ops.drift_proxy            # écoute sur LLM_PROXY_PORT (8080)
"""