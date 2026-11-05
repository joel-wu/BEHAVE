module compression_flac_entropy_flac_rice_decode (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_byte,
    input  logic [3:0]  in_nbits,
    input  logic        in_last,
    input  logic [4:0]  in_parameter,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_value,
    output logic        out_success
);
    logic active, failed, busy, final_byte;
    logic [4:0] parameter_q, remainder_left;
    logic [31:0] quotient, remainder_q;
    logic [7:0] byte_q;
    logic [3:0] bits_left;
    wire room = !out_valid || out_ready;
    wire [31:0] next_remainder = {remainder_q[30:0], byte_q[7]};
    wire [31:0] folded = (quotient << parameter_q) | next_remainder;
    assign in_ready = !busy && room;

    always_ff @(posedge clk) begin
        if (rst) begin
            active <= 1'b0;
            failed <= 1'b0;
            busy <= 1'b0;
            final_byte <= 1'b0;
            parameter_q <= 5'd0;
            remainder_left <= 5'd0;
            quotient <= 32'd0;
            remainder_q <= 32'd0;
            byte_q <= 8'd0;
            bits_left <= 4'd0;
            out_valid <= 1'b0;
            out_value <= 32'd0;
            out_success <= 1'b0;
        end else begin
            if (out_valid && out_ready)
                out_valid <= 1'b0;
            if (in_valid && in_ready) begin
                if (!active) begin
                    active <= 1'b1;
                    parameter_q <= in_parameter;
                end
                byte_q <= in_byte;
                final_byte <= in_last;
                bits_left <= in_nbits;
                busy <= 1'b1;
                if (failed) begin
                    bits_left <= 4'd0;
                end else if (in_nbits > 8 || (!in_last && in_nbits != 8)) begin
                    failed <= 1'b1;
                    bits_left <= 4'd0;
                    out_valid <= 1'b1;
                    out_value <= 32'd0;
                    out_success <= 1'b0;
                end
            end else if (busy && room) begin
                if (bits_left == 0) begin
                    busy <= 1'b0;
                    if (final_byte) begin
                        if (!failed && (quotient != 0 || remainder_left != 0)) begin
                            out_valid <= 1'b1;
                            out_value <= 32'd0;
                            out_success <= 1'b0;
                        end
                        active <= 1'b0;
                        failed <= 1'b0;
                        quotient <= 32'd0;
                        remainder_q <= 32'd0;
                        remainder_left <= 5'd0;
                    end
                end else begin
                    byte_q <= {byte_q[6:0], 1'b0};
                    bits_left <= bits_left - 4'd1;
                    if (remainder_left != 0) begin
                        remainder_q <= next_remainder;
                        remainder_left <= remainder_left - 5'd1;
                        if (remainder_left == 1) begin
                            out_valid <= 1'b1;
                            out_success <= 1'b1;
                            out_value <= (folded >> 1) ^ {32{folded[0]}};
                            quotient <= 32'd0;
                            remainder_q <= 32'd0;
                        end
                    end else if (!byte_q[7]) begin
                        if (quotient == (32'hFFFFFFFF >> parameter_q)) begin
                            failed <= 1'b1;
                            bits_left <= 4'd0;
                            out_valid <= 1'b1;
                            out_value <= 32'd0;
                            out_success <= 1'b0;
                        end else begin
                            quotient <= quotient + 32'd1;
                        end
                    end else if (parameter_q != 0) begin
                        remainder_left <= parameter_q;
                        remainder_q <= 32'd0;
                    end else begin
                        out_valid <= 1'b1;
                        out_success <= 1'b1;
                        out_value <= (quotient >> 1) ^ {32{quotient[0]}};
                        quotient <= 32'd0;
                    end
                end
            end
        end
    end
endmodule
