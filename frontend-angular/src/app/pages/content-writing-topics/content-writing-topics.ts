import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink, Router } from '@angular/router';

import { ApiError } from '../../core/services/api.service';
import { ModalService } from '../../core/services/modal.service';
import {
  HrContentService,
  WritingTopicOut,
  BulkImportResult,
} from '../../core/services/hr-content.service';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Writing topics management — list, create, edit, delete, bulk-import.
 * Each topic is an essay prompt with a min/max word range.
 */
@Component({
  selector: 'app-content-writing-topics',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, Topnav, Footer],
  templateUrl: './content-writing-topics.html',
  styleUrl: './content-writing-topics.css',
})
export class ContentWritingTopics implements OnInit {
  private contentSvc = inject(HrContentService);
  private modal = inject(ModalService);
  private router = inject(Router);

  topics = signal<WritingTopicOut[]>([]);
  loading = signal(true);
  loadError = signal('');

  filterDifficulty = signal<'intermediate' | 'expert' | ''>('');

  formOpen = signal(false);
  formMode = signal<'create' | 'edit'>('create');
  editingId = signal<number | null>(null);

  formPromptText = signal('');
  formDifficulty = signal<'intermediate' | 'expert'>('intermediate');
  formMinWords = signal<number>(100);
  formMaxWords = signal<number>(300);
  formCategory = signal('');

  formSubmitting = signal(false);
  formError = signal('');

  csvOpen = signal(false);
  csvFile = signal<File | null>(null);
  csvSubmitting = signal(false);
  csvResult = signal<BulkImportResult | null>(null);
  csvError = signal('');

  ngOnInit(): void { this.loadTopics(); }

  private loadTopics(): void {
    this.loading.set(true);
    this.loadError.set('');
    this.contentSvc.listWritingTopics().subscribe({
      next: (t) => { this.topics.set(t); this.loading.set(false); },
      error: (err: ApiError) => {
        if (err.status === 401) { this.router.navigate(['/login']); return; }
        this.loadError.set(err.message || 'Could not load writing topics.');
        this.loading.set(false);
      },
    });
  }

  filteredTopics = computed(() => {
    const all = this.topics();
    const d = this.filterDifficulty();
    return d ? all.filter(t => t.difficulty === d) : all;
  });

  truncate(text: string, max = 100): string {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }

  openCreateForm(): void {
    this.formMode.set('create');
    this.editingId.set(null);
    this.formPromptText.set('');
    this.formDifficulty.set('intermediate');
    this.formMinWords.set(100);
    this.formMaxWords.set(300);
    this.formCategory.set('');
    this.formError.set('');
    this.formOpen.set(true);
  }

  openEditForm(t: WritingTopicOut): void {
    this.formMode.set('edit');
    this.editingId.set(t.id);
    this.formPromptText.set(t.prompt_text);
    this.formDifficulty.set(t.difficulty as 'intermediate' | 'expert');
    this.formMinWords.set(t.min_words);
    this.formMaxWords.set(t.max_words);
    this.formCategory.set(t.category ?? '');
    this.formError.set('');
    this.formOpen.set(true);
  }

  closeForm(): void {
    if (this.formSubmitting()) return;
    this.formOpen.set(false);
  }

  private validateForm(): string | null {
    if (!this.formPromptText().trim()) return 'Prompt text is required.';
    const min = this.formMinWords();
    const max = this.formMaxWords();
    if (min < 50) return 'Minimum word count must be at least 50.';
    if (max > 1000) return 'Maximum word count cannot exceed 1000.';
    if (min >= max) return 'Minimum word count must be less than maximum.';
    return null;
  }

  submitForm(): void {
    const err = this.validateForm();
    if (err) { this.formError.set(err); return; }
    this.formError.set('');
    this.formSubmitting.set(true);

    if (this.formMode() === 'create') {
      this.contentSvc.createWritingTopic({
        prompt_text: this.formPromptText().trim(),
        difficulty: this.formDifficulty(),
        min_words: this.formMinWords(),
        max_words: this.formMaxWords(),
        category: this.formCategory().trim() || null,
      }).subscribe({
        next: (created) => {
          this.topics.update(arr => [created, ...arr]);
          this.formSubmitting.set(false);
          this.formOpen.set(false);
        },
        error: (err: ApiError) => {
          this.formSubmitting.set(false);
          this.formError.set(err.message || 'Could not create writing topic.');
        },
      });
    } else {
      const id = this.editingId();
      if (id === null) { this.formSubmitting.set(false); return; }
      this.contentSvc.updateWritingTopic(id, {
        prompt_text: this.formPromptText().trim(),
        difficulty: this.formDifficulty(),
        min_words: this.formMinWords(),
        max_words: this.formMaxWords(),
        category: this.formCategory().trim() || null,
      }).subscribe({
        next: (updated) => {
          this.topics.update(arr => arr.map(t => t.id === updated.id ? updated : t));
          this.formSubmitting.set(false);
          this.formOpen.set(false);
        },
        error: (err: ApiError) => {
          this.formSubmitting.set(false);
          this.formError.set(err.message || 'Could not update writing topic.');
        },
      });
    }
  }

  async onDelete(t: WritingTopicOut): Promise<void> {
    const ok = await this.modal.confirm(
      `Delete this writing topic?\n\n"${this.truncate(t.prompt_text, 100)}"\n\nThis cannot be undone.`,
      { okText: 'Delete', cancelText: 'Cancel', dangerous: true, title: 'Confirm delete' }
    );
    if (!ok) return;

    this.contentSvc.deleteWritingTopic(t.id).subscribe({
      next: () => {
        this.topics.update(arr => arr.filter(x => x.id !== t.id));
      },
      error: async (err: ApiError) => {
        await this.modal.alert(
          err.message || 'Could not delete writing topic.',
          { title: err.status === 409 ? 'Cannot delete — topic in use' : 'Delete failed' }
        );
      },
    });
  }

  openCsvModal(): void {
    this.csvFile.set(null);
    this.csvResult.set(null);
    this.csvError.set('');
    this.csvOpen.set(true);
  }

  closeCsvModal(): void {
    if (this.csvSubmitting()) return;
    this.csvOpen.set(false);
  }

  onCsvFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.csvFile.set(file);
    this.csvError.set('');
    this.csvResult.set(null);
  }

  submitCsv(): void {
    const file = this.csvFile();
    if (!file) { this.csvError.set('Choose a CSV file first.'); return; }
    this.csvSubmitting.set(true);
    this.csvError.set('');

    this.contentSvc.bulkImportWritingTopics(file).subscribe({
      next: (result) => {
        this.csvResult.set(result);
        this.csvSubmitting.set(false);
        if (result.created > 0) {
          this.contentSvc.listWritingTopics().subscribe({
            next: (t) => this.topics.set(t),
            error: () => { /* keep old list */ },
          });
        }
      },
      error: (err: ApiError) => {
        this.csvSubmitting.set(false);
        this.csvError.set(err.message || 'Upload failed.');
      },
    });
  }

  trackById = (_: number, t: WritingTopicOut) => t.id;
}