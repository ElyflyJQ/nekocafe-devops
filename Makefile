.PHONY: up down test test-member test-all lint lint-docker clean logs ps

up:     ; docker compose up -d --build
down:   ; docker compose down -v
test:   ; docker compose exec reservation pytest -v
test-member: ; docker compose exec member npm test
test-all: test test-member
lint:   ; docker run --rm -v $(PWD):/app -w /app hadolint/hadolint hadolint services/*/Dockerfile
logs:   ; docker compose logs -f
ps:     ; docker compose ps
clean:  down
	@docker system prune -f
