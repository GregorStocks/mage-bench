import { loadBlunderInternalsData } from '../../../utils/load-internals-dashboard-data';

export const prerender = true;

export function GET() {
  return Response.json(loadBlunderInternalsData());
}
