# Stage 1: Build Next.js frontend
FROM node:20.11 AS build-stage
WORKDIR /app

# Install dependencies
COPY ./web/package*.json ./
RUN npm install
COPY ./web .

# Compile Next.js and configure distDir
RUN sed -i 's/distDir:.*/distDir: process.env.NEXT_DIST_DIR || ".\/ui"/' next.config.mjs
ENV NODE_ENV production
ENV NEXT_DIST_DIR ./NEXT_BUILD
RUN npm run build

# Stage 2: Set up the Python environment
FROM python:3.10-slim
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Next.js build artifacts from the build stage
COPY --from=build-stage /app/NEXT_BUILD /app/ui

COPY src src
COPY search4all.py .

# Set environment variables
ENV PORT 8800
ENV BACKEND=""
ENV OPENAI_API_KEY=""
ENV GROQ_API_KEY=""
ENV ANTHROPIC_API_KEY=""
ENV OPENAI_BASE_URL=""
ENV LLM_MODEL=""
ENV KV_NAME=""
ENV RELATED_QUESTIONS=""
ENV CHAT_HISTORY=""
ENV NEXT_PUBLIC_GOOGLE_ANALYTICS=""

# API keys
ENV BING_SEARCH_V7_SUBSCRIPTION_KEY=""
ENV GOOGLE_SEARCH_API_KEY=""
ENV SERPER_SEARCH_API_KEY=""
ENV SEARCHAPI_API_KEY=""
ENV SEARCH1API_KEY=""

# Expose port
EXPOSE 8800

# Start command
CMD ["python", "search4all.py"]
