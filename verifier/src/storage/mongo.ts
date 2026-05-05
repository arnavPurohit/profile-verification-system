import { MongoClient, type Db } from "mongodb";

export async function connectMongo(url: string, dbName: string): Promise<Db> {
  const client = new MongoClient(url);
  await client.connect();
  return client.db(dbName);
}
