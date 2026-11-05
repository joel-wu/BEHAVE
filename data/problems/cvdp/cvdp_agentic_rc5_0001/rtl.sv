module rc5_enc_16bit (
    input logic clock, reset, enc_start,
    input logic [15:0] p,
    output logic [15:0] c,
    output logic enc_done
);
    logic [1:0] phase;
    logic [7:0] ca, a, b, s2, s3;
    wire [7:0] ca1 = next_ca(ca);
    wire [7:0] ca2 = next_ca(ca1);
    wire [7:0] ca3 = next_ca(ca2);

    // Exact CA_1 null-boundary topology supplied with the original task.
    function automatic logic [7:0] next_ca(input logic [7:0] x);
        next_ca = {x[6], x[7]^x[5], x[6]^x[5]^x[4], x[5]^x[3],
                   x[4]^x[3]^x[2], x[3]^x[1], x[2]^x[1]^x[0], x[1]};
    endfunction

    function automatic logic [7:0] rotate(input logic [7:0] x, input logic [2:0] n);
        rotate = (x << n) | (x >> (4'd8-{1'b0,n}));
    endfunction

    always_ff @(posedge clock) begin
        if (!reset) begin
            phase <= 0; ca <= 8'hff; a <= 0; b <= 0; s2 <= 0; s3 <= 0;
            c <= 0; enc_done <= 0;
        end else begin
            ca <= ca1;
            enc_done <= 0;
            case (phase)
                0: if (enc_start) begin
                    a <= p[15:8]+ca; b <= p[7:0]+ca1;
                    s2 <= ca2; s3 <= ca3; phase <= 1;
                end
                1: begin a <= rotate(a^b,b[2:0])+s2; phase <= 2; end
                2: begin b <= rotate(b^a,a[2:0])+s3; phase <= 3; end
                3: begin c <= {a,b}; enc_done <= 1; phase <= 0; end
            endcase
        end
    end
endmodule
