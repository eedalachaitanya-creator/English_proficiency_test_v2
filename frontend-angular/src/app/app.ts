import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  // Root shell. All page content renders inside <router-outlet>
  // based on the active route. See app.routes.ts for the route table.
}