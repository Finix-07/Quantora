package backtest

import (
	"context"
	"fmt"
	"sort"
	"sync"
)

// MemoryStore keeps completed backtests in process memory.
//
// This is the M2 walking skeleton: architecture.md defers experiment
// persistence to M3.4/M3.5, and standing up a PostgreSQL schema before the
// experiment model is designed would mean migrating it immediately. The
// consequence is stated plainly to the user in the API's response — a backtest
// retrieved by ID does not survive a restart until experiments land.
type MemoryStore struct {
	mu      sync.RWMutex
	records map[string]Record
	order   []string
	// capacity bounds memory for a long-running local process. Old records are
	// evicted oldest-first, and a request for an evicted ID gets ErrNotFound
	// rather than a stale or partial result.
	capacity int
}

func NewMemoryStore(capacity int) *MemoryStore {
	if capacity <= 0 {
		capacity = 200
	}
	return &MemoryStore{records: make(map[string]Record), capacity: capacity}
}

func (s *MemoryStore) Save(_ context.Context, record Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, exists := s.records[record.ID]; !exists {
		s.order = append(s.order, record.ID)
	}
	s.records[record.ID] = record

	for len(s.order) > s.capacity {
		oldest := s.order[0]
		s.order = s.order[1:]
		delete(s.records, oldest)
	}
	return nil
}

func (s *MemoryStore) Get(_ context.Context, id string) (Record, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	record, ok := s.records[id]
	if !ok {
		return Record{}, fmt.Errorf("%w: %s", ErrNotFound, id)
	}
	return record, nil
}

func (s *MemoryStore) List(_ context.Context, limit int) ([]Record, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	records := make([]Record, 0, len(s.records))
	for _, record := range s.records {
		records = append(records, record)
	}
	sort.Slice(records, func(i, j int) bool {
		return records[i].CreatedAt.After(records[j].CreatedAt)
	})
	if len(records) > limit {
		records = records[:limit]
	}
	return records, nil
}
