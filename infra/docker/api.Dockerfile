# Go application API.
# Build context: repository root.

FROM golang:1.27-alpine AS build
WORKDIR /src

# Dependencies are copied first so `go mod download` is cached independently of
# source changes — otherwise every edit re-downloads the module graph.
COPY apps/api/go.mod apps/api/go.sum ./
RUN go mod download

COPY apps/api/ ./

ARG VERSION=dev
# CGO is off so the result is a static binary that runs on a bare distroless
# base. -s -w drops the symbol table; the version is stamped in so /healthz and
# every saved experiment can report which build produced a result
# (architecture.md §9).
RUN CGO_ENABLED=0 GOOS=linux go build \
    -ldflags="-s -w -X github.com/anubhavjha/ai-quant-terminal/apps/api/internal/httpapi.Version=${VERSION}" \
    -o /out/api ./cmd/api

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=build /out/api /app/api
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/app/api"]
