this project has been deployed on free instance of Render cloud service, please note that due to free limits it may take some time to load and might be slow:
(Project Link here)[https://typescript-agentic-rag-frontend.onrender.com]

## Project Motive:

This Project was an attempt to revise my skills as software developer so i had challenged myself to only use free resources while ensuring the end result to be stable

## Project Technical Details:

- Backend: The Backend Side has been deployed seperatly and its services are Listed below:
  **Raw File Uploads**: S3-compatible object storage (MinIO in local dev, Backblaze B2 in production — accessed via the boto3 S3 API)
  **VectorDB**: Pinecone
  **LLM**: Google Gemini API (Note: Multple Models used)
  **Tokenizer**: Open source Model used (The Model is being accessed via HuggingFace)
  **Web Search**: DuckDuckGo Search API

- Frontend: The Front part was mostly developed using help of AI services but i do have basic Knowledge. The frontend is a nextjs project in typescript with few addidional libraries for UI components to function it without complexity

## Improvements To Be Made:

the Project was only made using Free resources so on backend side the multi-agent architecture would consume lot of request and hence it is not suitable for free tier of commercial LLM APIs so if paid API is obtained then posiblities to add new features can be rapidly increase.

The Backend Logic and Project Sturture can be improved using feedback of some experienced engineers

The Hosting platform could be changed from Render to AWS and CD/CI pipeline could be established using Github actions or jenkins.
