# lab2_desk: claims handler com LangChain e OpenRouter

O mesmo trabalho da `lab1_desk`, mas o agente é um `create_agent` do LangChain e o modelo vem do OpenRouter.
O código está no notebook `labs/week1day5_mex_2.ipynb`.

```
claims/queue/      as claims da fila (um arquivo por claim)
claims/held_out/   um segundo conjunto, para conferir depois
customers/         um arquivo por cliente
policies/          policies.json, plans.json, company.json, policy_wording.md, claims_manual.md
decisions/         o agente grava decisions/<claim id>.json aqui
tasks/             a lista de tarefas do agente por claim, tasks/<claim id>.md
handover.md        escrito quando todas as claims têm decisão
```

As entradas são cópias das da `lab1_desk`. Os resultados ficam separados, então os dois agentes podem ser comparados.

## Rodar

1. No VS Code, abra o notebook e escolha o kernel **`lab2_desk/.venv`** (já tem LangChain 1.x). Se ele não aparecer, rode
   `python -m venv .venv` e `.venv\Scripts\python.exe -m pip install -r requirements.txt` aqui dentro.
2. A chave `OPENROUTER_API_KEY` fica no `.env` da raiz do repositório.
3. Rode as células de cima para baixo até o `preflight()` terminar com "Tudo certo". Só então rode a Parte 2.

O modelo se escolhe na chamada, por exemplo `work_claims(model="openai/gpt-4o-mini", reasoning_effort="medium")`.
O padrão é `anthropic/claude-haiku-5-5`, o mesmo modelo da `lab1_desk`, para a comparação ser justa.

As decisões humanas ficam em `data/evals/` e não foram copiadas para cá, então o agente não as vê.
