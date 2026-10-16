;;; INVLIN - invierte sentido de objetos lineales manteniendo posicion y longitud.
;;; Civil 3D 2025 / AutoCAD 2025.
;;; Uso: APPLOAD -> InvertirLineas.lsp -> comando INVLIN
;;;
;;; LINE, ARC, ELLIPSE : geometria directa (entmod).
;;; LWPOLYLINE, POLYLINE (2D/3D/mallas de curva), SPLINE, HELIX, y otros
;;; objetos que acepte el comando nativo REVERSE : REVERSE.

(defun invlin:ang (p) (atan (cadr p) (car p)))

(defun invlin:pol (c r a)
  (list (+ (car c) (* r (cos a))) (+ (cadr c) (* r (sin a))) (caddr c))
)

;; ARC: mismo trazo recorrido al reves = CCW respecto a normal opuesta.
(defun invlin:arc (ed / n nn c r a1 a2 cw sw ew)
  (setq n  (or (cdr (assoc 210 ed)) '(0.0 0.0 1.0))
        nn (mapcar '- n)
        c  (cdr (assoc 10 ed))
        r  (cdr (assoc 40 ed))
        a1 (cdr (assoc 50 ed))
        a2 (cdr (assoc 51 ed))
        cw (trans c n 0)
        sw (trans (invlin:pol c r a1) n 0)
        ew (trans (invlin:pol c r a2) n 0)
  )
  (entmod
    (subst (cons 51 (invlin:ang (trans sw 0 nn)))
      (assoc 51 ed)
      (subst (cons 50 (invlin:ang (trans ew 0 nn)))
        (assoc 50 ed)
        (subst (cons 10 (trans cw 0 nn))
          (assoc 10 ed)
          (subst (cons 210 nn) (assoc 210 ed) ed)
        )
      )
    )
  )
)

;; ELLIPSE: normal opuesta => parametro t pasa a -t.
(defun invlin:ell (ed / n s e pi2)
  (setq n   (cdr (assoc 210 ed))
        s   (cdr (assoc 41 ed))
        e   (cdr (assoc 42 ed))
        pi2 (* 2.0 pi)
  )
  (entmod
    (subst (cons 42 (- pi2 s))
      (assoc 42 ed)
      (subst (cons 41 (- pi2 e))
        (assoc 41 ed)
        (subst (cons 210 (mapcar '- n)) (assoc 210 ed) ed)
      )
    )
  )
)

(defun c:INVLIN (/ ss i ent ed typ p1 p2 n other cmd)
  (prompt "\nSeleccione objetos a invertir (lineas, arcos, polilineas 2D/3D, splines, elipses...): ")
  (if (setq ss (ssget '((0 . "LINE,ARC,ELLIPSE,LWPOLYLINE,POLYLINE,SPLINE,HELIX"))))
    (progn
      (setq i 0 n 0 other (ssadd) cmd (getvar "CMDECHO"))
      (repeat (sslength ss)
        (setq ent (ssname ss i)
              ed  (entget ent)
              typ (cdr (assoc 0 ed))
              i   (1+ i))
        (cond
          ((= typ "LINE")
           (setq p1 (cdr (assoc 10 ed)) p2 (cdr (assoc 11 ed)))
           (if (entmod (subst (cons 11 p1) (assoc 11 ed)
                              (subst (cons 10 p2) (assoc 10 ed) ed)))
             (setq n (1+ n))))
          ((= typ "ARC")
           (if (invlin:arc ed) (setq n (1+ n))))
          ((= typ "ELLIPSE")
           (if (invlin:ell ed) (setq n (1+ n))))
          (T (ssadd ent other))
        )
      )
      (if (> (sslength other) 0)
        (progn
          (setvar "CMDECHO" 0)
          (command "_.REVERSE" other "")
          (setvar "CMDECHO" cmd)
          (setq n (+ n (sslength other)))
        )
      )
      (prompt (strcat "\n" (itoa n) " objeto(s) invertido(s). Objetos en capas bloqueadas se omiten."))
    )
  )
  (princ)
)
(prompt "\nComando INVLIN cargado.")
(princ)
