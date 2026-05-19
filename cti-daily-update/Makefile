-include .env
export

.PHONY: build deploy test-nvd invoke-nvd

define update_ssm_if_changed
	current_val=$$(aws ssm get-parameter --name "$(1)" --with-decryption --query "Parameter.Value" --output text 2>/dev/null || echo ""); \
	if [ "$$current_val" != "$(2)" ]; then \
		echo "Updating SSM parameter $(1)"; \
		aws ssm put-parameter --name "$(1)" --value "$(2)" --type "String" --overwrite --no-cli-pager; \
	else \
		echo "SSM parameter $(1) is up to date"; \
	fi
endef	

build:
	@echo "Building the deployment package..."
	uv pip compile pyproject.toml -o requirements.txt --system-certs
	sam build

deploy: build
	@echo "Deploying the Lambda function..."
	@$(call update_ssm_if_changed,/cve-scan/nvd-api-key,$(NVD_API_KEY))
	@$(call update_ssm_if_changed,/cve-scan/excel-logic-app-webhook-url,$(EXCEL_LOGIC_APP_WEBHOOK_URL))
	@$(call update_ssm_if_changed,/cve-scan/email-logic-app-webhook-url,$(EMAIL_LOGIC_APP_WEBHOOK_URL))
	@$(call update_ssm_if_changed,/cve-scan/gpt-key-b64,$(GPT_KEY_B64))
	@$(call update_ssm_if_changed,/cve-scan/openai-url,$(OPENAI_URL))
	@if [ -f samconfig.toml ]; then \
		sam deploy --config-file samconfig.toml; \
	else \
		sam deploy --guided; \
	fi

test-nvd: export IS_DRY_RUN=true
test-nvd:
	@echo "Testing the NVD Lambda function locally..."
	uv run python -m functions.nvd

test-news: export IS_DRY_RUN=true
test-news:
	@echo "Testing the News Lambda function locally..."
	uv run python -m functions.news

invoke-nvd: build
	@echo "Running the NVD Lambda function locally..."
	sam local invoke NVDLambdaFunction

invoke-news: build
	@echo "Running the News Lambda function locally..."
	sam local invoke NewsLambdaFunction