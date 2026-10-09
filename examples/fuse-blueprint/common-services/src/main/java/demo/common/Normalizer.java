package demo.common;

public class Normalizer {
	public String clean(String body) {
		return body == null ? null : body.trim();
	}
}
