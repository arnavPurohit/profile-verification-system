FROM node:20-alpine AS build
WORKDIR /srv
COPY package.json tsconfig.json ./
RUN npm install --omit=dev=false
COPY src ./src
RUN npx tsc -p .

FROM node:20-alpine
WORKDIR /srv
COPY --from=build /srv/package.json /srv/package-lock.json* ./
RUN npm install --omit=dev
COPY --from=build /srv/dist ./dist
EXPOSE 8000
CMD ["node", "dist/server.js"]
