import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams, HttpHeaders } from '@angular/common/http';
import { Observable, catchError } from 'rxjs';
import { environment } from '../../../environments/environment';

import { ApiService, ApiError } from './api.service';

/**
 * HR-side content authoring service. Wraps the /api/hr/content/* endpoints
 * defined in backend/routes/hr_content.py.
 *
 * All four entity types (passages, questions, writing-topics, speaking-topics)
 * follow the same CRUD pattern, so most methods are duplicated across them.
 * If we add a fifth entity type later, copy the passage block and rename.
 */

// ---------- HR-facing model types ----------
// These INCLUDE correct_answer for questions, unlike the candidate-facing
// QuestionPublic type in test.models.ts. Keep them separate so we don't
// accidentally leak correct_answer to the candidate flow.

export interface PassageOut {
  id: number;
  title: string;
  body: string;
  difficulty: 'intermediate' | 'expert';
  topic: string | null;
  word_count: number;
}

export interface PassageCreate {
  title: string;
  body: string;
  difficulty: 'intermediate' | 'expert';
  topic?: string | null;
}

export interface PassageUpdate {
  title?: string;
  body?: string;
  difficulty?: 'intermediate' | 'expert';
  topic?: string | null;
}

export type QuestionType = 'reading_comp' | 'grammar' | 'vocabulary' | 'fill_blank';

export interface QuestionOut {
  id: number;
  question_type: QuestionType;
  difficulty: 'intermediate' | 'expert';
  stem: string;
  options: string[];
  correct_answer: number;
  passage_id: number | null;
}

export interface QuestionCreate {
  question_type: QuestionType;
  difficulty: 'intermediate' | 'expert';
  stem: string;
  options: string[];
  correct_answer: number;
  passage_id?: number | null;
}

export interface QuestionUpdate {
  stem?: string;
  difficulty?: 'intermediate' | 'expert';
  options?: string[];
  correct_answer?: number;
}

export interface WritingTopicOut {
  id: number;
  prompt_text: string;
  difficulty: 'intermediate' | 'expert';
  min_words: number;
  max_words: number;
  category: string | null;
}

export interface WritingTopicCreate {
  prompt_text: string;
  difficulty: 'intermediate' | 'expert';
  min_words: number;
  max_words: number;
  category?: string | null;
}

export interface WritingTopicUpdate {
  prompt_text?: string;
  difficulty?: 'intermediate' | 'expert';
  min_words?: number;
  max_words?: number;
  category?: string | null;
}

export interface SpeakingTopicOut {
  id: number;
  prompt_text: string;
  difficulty: 'intermediate' | 'expert';
  category: string | null;
}

export interface SpeakingTopicCreate {
  prompt_text: string;
  difficulty: 'intermediate' | 'expert';
  category?: string | null;
}

export interface SpeakingTopicUpdate {
  prompt_text?: string;
  difficulty?: 'intermediate' | 'expert';
  category?: string | null;
}

export interface BulkImportResult {
  created: number;
  errors: string[];
}

@Injectable({ providedIn: 'root' })
export class HrContentService {
  private api = inject(ApiService);
  private http = inject(HttpClient);

  // ApiService doesn't expose a baseUrl getter, so we inline the base
  // for the few calls (DELETE, PATCH, multipart upload) that need
  // direct HttpClient access.
  private readonly baseUrl = environment.apiUrl;

  // ---------- PASSAGES ----------
  listPassages(difficulty?: string): Observable<PassageOut[]> {
    const path = difficulty
      ? `/api/hr/content/passages?difficulty=${encodeURIComponent(difficulty)}`
      : '/api/hr/content/passages';
    return this.api.get<PassageOut[]>(path);
  }

  createPassage(payload: PassageCreate): Observable<PassageOut> {
    return this.api.post<PassageOut>('/api/hr/content/passages', payload);
  }

  updatePassage(id: number, payload: PassageUpdate): Observable<PassageOut> {
    return this.http
      .patch<PassageOut>(`${this.baseUrl}/api/hr/content/passages/${id}`, payload, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  deletePassage(id: number): Observable<void> {
    return this.http
      .delete<void>(`${this.baseUrl}/api/hr/content/passages/${id}`, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  bulkImportPassages(file: File): Observable<BulkImportResult> {
    const fd = new FormData();
    fd.append('file', file);
    return this.api.post<BulkImportResult>('/api/hr/content/passages/bulk', fd);
  }

  // ---------- QUESTIONS ----------
  listQuestions(filters: {
    type?: QuestionType;
    difficulty?: string;
    passage_id?: number;
  } = {}): Observable<QuestionOut[]> {
    const parts: string[] = [];
    if (filters.type) parts.push(`type=${encodeURIComponent(filters.type)}`);
    if (filters.difficulty) parts.push(`difficulty=${encodeURIComponent(filters.difficulty)}`);
    if (filters.passage_id !== undefined) parts.push(`passage_id=${filters.passage_id}`);
    const qs = parts.length ? `?${parts.join('&')}` : '';
    return this.api.get<QuestionOut[]>(`/api/hr/content/questions${qs}`);
  }

  createQuestion(payload: QuestionCreate): Observable<QuestionOut> {
    return this.api.post<QuestionOut>('/api/hr/content/questions', payload);
  }

  updateQuestion(id: number, payload: QuestionUpdate): Observable<QuestionOut> {
    return this.http
      .patch<QuestionOut>(`${this.baseUrl}/api/hr/content/questions/${id}`, payload, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  deleteQuestion(id: number): Observable<void> {
    return this.http
      .delete<void>(`${this.baseUrl}/api/hr/content/questions/${id}`, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  bulkImportQuestions(file: File): Observable<BulkImportResult> {
    const fd = new FormData();
    fd.append('file', file);
    return this.api.post<BulkImportResult>('/api/hr/content/questions/bulk', fd);
  }

  // ---------- WRITING TOPICS ----------
  listWritingTopics(difficulty?: string): Observable<WritingTopicOut[]> {
    const path = difficulty
      ? `/api/hr/content/writing-topics?difficulty=${encodeURIComponent(difficulty)}`
      : '/api/hr/content/writing-topics';
    return this.api.get<WritingTopicOut[]>(path);
  }

  createWritingTopic(payload: WritingTopicCreate): Observable<WritingTopicOut> {
    return this.api.post<WritingTopicOut>('/api/hr/content/writing-topics', payload);
  }

  updateWritingTopic(id: number, payload: WritingTopicUpdate): Observable<WritingTopicOut> {
    return this.http
      .patch<WritingTopicOut>(`${this.baseUrl}/api/hr/content/writing-topics/${id}`, payload, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  deleteWritingTopic(id: number): Observable<void> {
    return this.http
      .delete<void>(`${this.baseUrl}/api/hr/content/writing-topics/${id}`, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  bulkImportWritingTopics(file: File): Observable<BulkImportResult> {
    const fd = new FormData();
    fd.append('file', file);
    return this.api.post<BulkImportResult>('/api/hr/content/writing-topics/bulk', fd);
  }

  // ---------- SPEAKING TOPICS ----------
  // No bulk import for speaking topics — typically only ~8 of them total,
  // single form is faster than CSV. (Backend route doesn't exist either.)
  listSpeakingTopics(difficulty?: string): Observable<SpeakingTopicOut[]> {
    const path = difficulty
      ? `/api/hr/content/speaking-topics?difficulty=${encodeURIComponent(difficulty)}`
      : '/api/hr/content/speaking-topics';
    return this.api.get<SpeakingTopicOut[]>(path);
  }

  createSpeakingTopic(payload: SpeakingTopicCreate): Observable<SpeakingTopicOut> {
    return this.api.post<SpeakingTopicOut>('/api/hr/content/speaking-topics', payload);
  }

  updateSpeakingTopic(id: number, payload: SpeakingTopicUpdate): Observable<SpeakingTopicOut> {
    return this.http
      .patch<SpeakingTopicOut>(`${this.baseUrl}/api/hr/content/speaking-topics/${id}`, payload, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  deleteSpeakingTopic(id: number): Observable<void> {
    return this.http
      .delete<void>(`${this.baseUrl}/api/hr/content/speaking-topics/${id}`, {
        withCredentials: true,
      })
      .pipe(catchError(err => this.toApiError(err)));
  }

  // ---------- helpers ----------
  /**
   * Normalise an HttpErrorResponse from raw HttpClient calls into our
   * ApiError shape, matching what ApiService.get/post would produce.
   */
  private toApiError(err: any): Observable<never> {
    const data = err?.error;
    let message: string;
    if (data && typeof data.detail === 'string') {
      message = data.detail;
    } else if (data && Array.isArray(data.detail)) {
      message = data.detail
        .map((e: { msg: string; loc?: (string | number)[] }) => {
          const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : '';
          return field ? `${field}: ${e.msg}` : e.msg;
        })
        .join('; ');
    } else if (err.status === 0) {
      message = 'Could not reach the server. Is the backend running on port 8000?';
    } else {
      message = err.statusText || `HTTP ${err.status}`;
    }
    throw new ApiError(message, err.status, data);
  }
}