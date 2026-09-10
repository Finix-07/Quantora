package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/anubhavjha/ai-quant-terminal/apps/api/internal/quant"
)

func postCompare(t *testing.T, body string) *httptest.ResponseRecorder {
	t.Helper()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/strategies/compare", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	// The client points at a port nothing listens on. Every case below is
	// rejected by validation *before* the client is called, so these assertions
	// do not depend on an engine being up; a request that passed validation
	// would surface as a 503, which is the honest answer when it is not.
	deps := RouterDeps{
		Logger: discardLogger(),
		Health: NewHealthRegistry(),
		Quant:  quant.New("http://127.0.0.1:1", 500*time.Millisecond, discardLogger()),
	}
	NewRouter(deps).ServeHTTP(rec, req)
	return rec
}

func decodeError(t *testing.T, rec *httptest.ResponseRecorder) ErrorBody {
	t.Helper()
	var body struct {
		Error ErrorBody `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode: %v (body=%s)", err, rec.Body.String())
	}
	return body.Error
}

func TestCompareValidation(t *testing.T) {
	tests := []struct {
		name    string
		body    string
		wantMsg string
	}{
		{
			name:    "single strategy",
			body:    `{"symbol":"RELIANCE.NS","start":"2023-01-01","end":"2023-12-31","strategies":[{"strategy":"macd"}]}`,
			wantMsg: "at least two entries",
		},
		{
			name:    "missing symbol",
			body:    `{"start":"2023-01-01","end":"2023-12-31","strategies":[{"strategy":"macd"},{"strategy":"bollinger"}]}`,
			wantMsg: "symbol is required",
		},
		{
			name:    "entry without a strategy name",
			body:    `{"symbol":"X","start":"2023-01-01","end":"2023-12-31","strategies":[{"strategy":"macd"},{"parameters":{}}]}`,
			wantMsg: "strategies[1] is missing a `strategy` name",
		},
		{
			name:    "reversed date range",
			body:    `{"symbol":"X","start":"2023-12-31","end":"2023-01-01","strategies":[{"strategy":"macd"},{"strategy":"bollinger"}]}`,
			wantMsg: "start must not be after end",
		},
		{
			name:    "malformed date",
			body:    `{"symbol":"X","start":"31/12/2023","end":"2023-01-01","strategies":[{"strategy":"macd"},{"strategy":"bollinger"}]}`,
			wantMsg: "must be ISO dates",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			rec := postCompare(t, tc.body)

			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400; body=%s", rec.Code, rec.Body.String())
			}
			got := decodeError(t, rec)
			if got.Code != CodeInvalidRequest {
				t.Errorf("code = %q, want %q", got.Code, CodeInvalidRequest)
			}
			if !strings.Contains(got.Message, tc.wantMsg) {
				t.Errorf("message = %q, want it to mention %q", got.Message, tc.wantMsg)
			}
		})
	}
}

func TestCompareReportsEveryProblemAtOnce(t *testing.T) {
	// One field per round trip would make fixing a bad request tedious.
	rec := postCompare(t, `{"start":"nope","end":"also-nope","strategies":[{"strategy":""}]}`)

	message := decodeError(t, rec).Message
	for _, want := range []string{"symbol is required", "at least two entries", "ISO dates"} {
		if !strings.Contains(message, want) {
			t.Errorf("message %q should mention %q", message, want)
		}
	}
}

func TestCompareRejectsUnknownFields(t *testing.T) {
	// A typo'd cost field that silently applied the default would produce a
	// comparison under different assumptions than the user asked for (NFR5.2).
	rec := postCompare(t,
		`{"symbol":"X","start":"2023-01-01","end":"2023-12-31","strategies":[{"strategy":"a"},{"strategy":"b"}],"allow_shrt":true}`)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
	if !strings.Contains(decodeError(t, rec).Message, "allow_shrt") {
		t.Errorf("the error should name the unknown field, got %q", decodeError(t, rec).Message)
	}
}

func TestCompareReportsAnUnreachableEngineAsSuchNotAsAValidationError(t *testing.T) {
	// A valid request with the engine down must not look like the user's
	// mistake. The two need different responses: check the stack, versus fix
	// the input.
	rec := postCompare(t,
		`{"symbol":"RELIANCE.NS","start":"2023-01-01","end":"2023-12-31","strategies":[{"strategy":"macd"},{"strategy":"bollinger"}]}`)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503; body=%s", rec.Code, rec.Body.String())
	}
	got := decodeError(t, rec)
	if got.Code != CodeUpstreamFailure {
		t.Errorf("code = %q, want %q", got.Code, CodeUpstreamFailure)
	}
	if !strings.Contains(got.Message, "docker compose ps") {
		t.Errorf("message %q should tell the user how to check the stack", got.Message)
	}
}
