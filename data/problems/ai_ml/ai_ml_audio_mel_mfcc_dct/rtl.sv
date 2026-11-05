module ai_ml_audio_mel_mfcc_dct (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_n_mfcc,
    input  logic [7:0]  in_n_mels,
    input  logic [7:0]  in_k,
    input  logic        in_norm_ortho,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_coeff_0,
    output logic [31:0] out_coeff_1,
    output logic [31:0] out_coeff_2,
    output logic [31:0] out_coeff_3,
    output logic [31:0] out_coeff_4,
    output logic [31:0] out_coeff_5,
    output logic [31:0] out_coeff_6,
    output logic [31:0] out_coeff_7,
    output logic [31:0] out_coeff_8,
    output logic [31:0] out_coeff_9,
    output logic [31:0] out_coeff_10,
    output logic [31:0] out_coeff_11,
    output logic [31:0] out_coeff_12,
    output logic [31:0] out_coeff_13,
    output logic [31:0] out_coeff_14,
    output logic [31:0] out_coeff_15
);

logic busy;
wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;
assign in_ready        = ~busy | output_consumed;
assign out_valid       = busy;

function automatic logic [31:0] lut_coeff(
    input logic [7:0] n_mfcc,
    input logic [7:0] n_mels,
    input logic [7:0] k,
    input logic       norm_ortho,
    input logic [4:0] idx
);
    logic [21:0] key;
    logic mels_legal;
    begin
        lut_coeff = 32'h00000000;
        mels_legal = (n_mels == 8'd1) || (n_mels == 8'd2) || (n_mels == 8'd4) || (n_mels == 8'd8);
        key = {norm_ortho, n_mels, k, idx};
        if (mels_legal && (k < 8'd8) && (k < n_mfcc) && ({3'b000, idx} < n_mels)) begin
            unique case (key)
                22'h002000: lut_coeff = 32'h40000000;
                22'h002020: lut_coeff = 32'h00000000;
                22'h002040: lut_coeff = 32'hC0000000;
                22'h002060: lut_coeff = 32'h00000000;
                22'h002080: lut_coeff = 32'h40000000;
                22'h0020A0: lut_coeff = 32'h00000000;
                22'h0020C0: lut_coeff = 32'hC0000000;
                22'h0020E0: lut_coeff = 32'h00000000;
                22'h004000: lut_coeff = 32'h40000000;
                22'h004001: lut_coeff = 32'h40000000;
                22'h004020: lut_coeff = 32'h3FB504F3;
                22'h004021: lut_coeff = 32'hBFB504F3;
                22'h004040: lut_coeff = 32'h00000000;
                22'h004041: lut_coeff = 32'h00000000;
                22'h004060: lut_coeff = 32'hBFB504F3;
                22'h004061: lut_coeff = 32'h3FB504F3;
                22'h004080: lut_coeff = 32'hC0000000;
                22'h004081: lut_coeff = 32'hC0000000;
                22'h0040A0: lut_coeff = 32'hBFB504F3;
                22'h0040A1: lut_coeff = 32'h3FB504F3;
                22'h0040C0: lut_coeff = 32'h00000000;
                22'h0040C1: lut_coeff = 32'h00000000;
                22'h0040E0: lut_coeff = 32'h3FB504F3;
                22'h0040E1: lut_coeff = 32'hBFB504F3;
                22'h008000: lut_coeff = 32'h40000000;
                22'h008001: lut_coeff = 32'h40000000;
                22'h008002: lut_coeff = 32'h40000000;
                22'h008003: lut_coeff = 32'h40000000;
                22'h008020: lut_coeff = 32'h3FEC835E;
                22'h008021: lut_coeff = 32'h3F43EF15;
                22'h008022: lut_coeff = 32'hBF43EF15;
                22'h008023: lut_coeff = 32'hBFEC835E;
                22'h008040: lut_coeff = 32'h3FB504F3;
                22'h008041: lut_coeff = 32'hBFB504F3;
                22'h008042: lut_coeff = 32'hBFB504F3;
                22'h008043: lut_coeff = 32'h3FB504F3;
                22'h008060: lut_coeff = 32'h3F43EF15;
                22'h008061: lut_coeff = 32'hBFEC835E;
                22'h008062: lut_coeff = 32'h3FEC835E;
                22'h008063: lut_coeff = 32'hBF43EF15;
                22'h008080: lut_coeff = 32'h00000000;
                22'h008081: lut_coeff = 32'h00000000;
                22'h008082: lut_coeff = 32'h00000000;
                22'h008083: lut_coeff = 32'h00000000;
                22'h0080A0: lut_coeff = 32'hBF43EF15;
                22'h0080A1: lut_coeff = 32'h3FEC835E;
                22'h0080A2: lut_coeff = 32'hBFEC835E;
                22'h0080A3: lut_coeff = 32'h3F43EF15;
                22'h0080C0: lut_coeff = 32'hBFB504F3;
                22'h0080C1: lut_coeff = 32'h3FB504F3;
                22'h0080C2: lut_coeff = 32'h3FB504F3;
                22'h0080C3: lut_coeff = 32'hBFB504F3;
                22'h0080E0: lut_coeff = 32'hBFEC835E;
                22'h0080E1: lut_coeff = 32'hBF43EF15;
                22'h0080E2: lut_coeff = 32'h3F43EF15;
                22'h0080E3: lut_coeff = 32'h3FEC835E;
                22'h010000: lut_coeff = 32'h40000000;
                22'h010001: lut_coeff = 32'h40000000;
                22'h010002: lut_coeff = 32'h40000000;
                22'h010003: lut_coeff = 32'h40000000;
                22'h010004: lut_coeff = 32'h40000000;
                22'h010005: lut_coeff = 32'h40000000;
                22'h010006: lut_coeff = 32'h40000000;
                22'h010007: lut_coeff = 32'h40000000;
                22'h010020: lut_coeff = 32'h3FFB14BE;
                22'h010021: lut_coeff = 32'h3FD4DB31;
                22'h010022: lut_coeff = 32'h3F8E39DA;
                22'h010023: lut_coeff = 32'h3EC7C5C2;
                22'h010024: lut_coeff = 32'hBEC7C5C2;
                22'h010025: lut_coeff = 32'hBF8E39DA;
                22'h010026: lut_coeff = 32'hBFD4DB31;
                22'h010027: lut_coeff = 32'hBFFB14BE;
                22'h010040: lut_coeff = 32'h3FEC835E;
                22'h010041: lut_coeff = 32'h3F43EF15;
                22'h010042: lut_coeff = 32'hBF43EF15;
                22'h010043: lut_coeff = 32'hBFEC835E;
                22'h010044: lut_coeff = 32'hBFEC835E;
                22'h010045: lut_coeff = 32'hBF43EF15;
                22'h010046: lut_coeff = 32'h3F43EF15;
                22'h010047: lut_coeff = 32'h3FEC835E;
                22'h010060: lut_coeff = 32'h3FD4DB31;
                22'h010061: lut_coeff = 32'hBEC7C5C2;
                22'h010062: lut_coeff = 32'hBFFB14BE;
                22'h010063: lut_coeff = 32'hBF8E39DA;
                22'h010064: lut_coeff = 32'h3F8E39DA;
                22'h010065: lut_coeff = 32'h3FFB14BE;
                22'h010066: lut_coeff = 32'h3EC7C5C2;
                22'h010067: lut_coeff = 32'hBFD4DB31;
                22'h010080: lut_coeff = 32'h3FB504F3;
                22'h010081: lut_coeff = 32'hBFB504F3;
                22'h010082: lut_coeff = 32'hBFB504F3;
                22'h010083: lut_coeff = 32'h3FB504F3;
                22'h010084: lut_coeff = 32'h3FB504F3;
                22'h010085: lut_coeff = 32'hBFB504F3;
                22'h010086: lut_coeff = 32'hBFB504F3;
                22'h010087: lut_coeff = 32'h3FB504F3;
                22'h0100A0: lut_coeff = 32'h3F8E39DA;
                22'h0100A1: lut_coeff = 32'hBFFB14BE;
                22'h0100A2: lut_coeff = 32'h3EC7C5C2;
                22'h0100A3: lut_coeff = 32'h3FD4DB31;
                22'h0100A4: lut_coeff = 32'hBFD4DB31;
                22'h0100A5: lut_coeff = 32'hBEC7C5C2;
                22'h0100A6: lut_coeff = 32'h3FFB14BE;
                22'h0100A7: lut_coeff = 32'hBF8E39DA;
                22'h0100C0: lut_coeff = 32'h3F43EF15;
                22'h0100C1: lut_coeff = 32'hBFEC835E;
                22'h0100C2: lut_coeff = 32'h3FEC835E;
                22'h0100C3: lut_coeff = 32'hBF43EF15;
                22'h0100C4: lut_coeff = 32'hBF43EF15;
                22'h0100C5: lut_coeff = 32'h3FEC835E;
                22'h0100C6: lut_coeff = 32'hBFEC835E;
                22'h0100C7: lut_coeff = 32'h3F43EF15;
                22'h0100E0: lut_coeff = 32'h3EC7C5C2;
                22'h0100E1: lut_coeff = 32'hBF8E39DA;
                22'h0100E2: lut_coeff = 32'h3FD4DB31;
                22'h0100E3: lut_coeff = 32'hBFFB14BE;
                22'h0100E4: lut_coeff = 32'h3FFB14BE;
                22'h0100E5: lut_coeff = 32'hBFD4DB31;
                22'h0100E6: lut_coeff = 32'h3F8E39DA;
                22'h0100E7: lut_coeff = 32'hBEC7C5C2;
                22'h202000: lut_coeff = 32'h3F800000;
                22'h202020: lut_coeff = 32'h00000000;
                22'h202040: lut_coeff = 32'hBFB504F3;
                22'h202060: lut_coeff = 32'h00000000;
                22'h202080: lut_coeff = 32'h3FB504F3;
                22'h2020A0: lut_coeff = 32'h00000000;
                22'h2020C0: lut_coeff = 32'hBFB504F3;
                22'h2020E0: lut_coeff = 32'h00000000;
                22'h204000: lut_coeff = 32'h3F3504F3;
                22'h204001: lut_coeff = 32'h3F3504F3;
                22'h204020: lut_coeff = 32'h3F3504F3;
                22'h204021: lut_coeff = 32'hBF3504F3;
                22'h204040: lut_coeff = 32'h00000000;
                22'h204041: lut_coeff = 32'h00000000;
                22'h204060: lut_coeff = 32'hBF3504F3;
                22'h204061: lut_coeff = 32'h3F3504F3;
                22'h204080: lut_coeff = 32'hBF800000;
                22'h204081: lut_coeff = 32'hBF800000;
                22'h2040A0: lut_coeff = 32'hBF3504F3;
                22'h2040A1: lut_coeff = 32'h3F3504F3;
                22'h2040C0: lut_coeff = 32'h00000000;
                22'h2040C1: lut_coeff = 32'h00000000;
                22'h2040E0: lut_coeff = 32'h3F3504F3;
                22'h2040E1: lut_coeff = 32'hBF3504F3;
                22'h208000: lut_coeff = 32'h3F000000;
                22'h208001: lut_coeff = 32'h3F000000;
                22'h208002: lut_coeff = 32'h3F000000;
                22'h208003: lut_coeff = 32'h3F000000;
                22'h208020: lut_coeff = 32'h3F273D75;
                22'h208021: lut_coeff = 32'h3E8A8BD4;
                22'h208022: lut_coeff = 32'hBE8A8BD4;
                22'h208023: lut_coeff = 32'hBF273D75;
                22'h208040: lut_coeff = 32'h3F000000;
                22'h208041: lut_coeff = 32'hBF000000;
                22'h208042: lut_coeff = 32'hBF000000;
                22'h208043: lut_coeff = 32'h3F000000;
                22'h208060: lut_coeff = 32'h3E8A8BD4;
                22'h208061: lut_coeff = 32'hBF273D75;
                22'h208062: lut_coeff = 32'h3F273D75;
                22'h208063: lut_coeff = 32'hBE8A8BD4;
                22'h208080: lut_coeff = 32'h00000000;
                22'h208081: lut_coeff = 32'h00000000;
                22'h208082: lut_coeff = 32'h00000000;
                22'h208083: lut_coeff = 32'h00000000;
                22'h2080A0: lut_coeff = 32'hBE8A8BD4;
                22'h2080A1: lut_coeff = 32'h3F273D75;
                22'h2080A2: lut_coeff = 32'hBF273D75;
                22'h2080A3: lut_coeff = 32'h3E8A8BD4;
                22'h2080C0: lut_coeff = 32'hBF000000;
                22'h2080C1: lut_coeff = 32'h3F000000;
                22'h2080C2: lut_coeff = 32'h3F000000;
                22'h2080C3: lut_coeff = 32'hBF000000;
                22'h2080E0: lut_coeff = 32'hBF273D75;
                22'h2080E1: lut_coeff = 32'hBE8A8BD4;
                22'h2080E2: lut_coeff = 32'h3E8A8BD4;
                22'h2080E3: lut_coeff = 32'h3F273D75;
                22'h210000: lut_coeff = 32'h3EB504F3;
                22'h210001: lut_coeff = 32'h3EB504F3;
                22'h210002: lut_coeff = 32'h3EB504F3;
                22'h210003: lut_coeff = 32'h3EB504F3;
                22'h210004: lut_coeff = 32'h3EB504F3;
                22'h210005: lut_coeff = 32'h3EB504F3;
                22'h210006: lut_coeff = 32'h3EB504F3;
                22'h210007: lut_coeff = 32'h3EB504F3;
                22'h210020: lut_coeff = 32'h3EFB14BE;
                22'h210021: lut_coeff = 32'h3ED4DB31;
                22'h210022: lut_coeff = 32'h3E8E39DA;
                22'h210023: lut_coeff = 32'h3DC7C5C2;
                22'h210024: lut_coeff = 32'hBDC7C5C2;
                22'h210025: lut_coeff = 32'hBE8E39DA;
                22'h210026: lut_coeff = 32'hBED4DB31;
                22'h210027: lut_coeff = 32'hBEFB14BE;
                22'h210040: lut_coeff = 32'h3EEC835E;
                22'h210041: lut_coeff = 32'h3E43EF15;
                22'h210042: lut_coeff = 32'hBE43EF15;
                22'h210043: lut_coeff = 32'hBEEC835E;
                22'h210044: lut_coeff = 32'hBEEC835E;
                22'h210045: lut_coeff = 32'hBE43EF15;
                22'h210046: lut_coeff = 32'h3E43EF15;
                22'h210047: lut_coeff = 32'h3EEC835E;
                22'h210060: lut_coeff = 32'h3ED4DB31;
                22'h210061: lut_coeff = 32'hBDC7C5C2;
                22'h210062: lut_coeff = 32'hBEFB14BE;
                22'h210063: lut_coeff = 32'hBE8E39DA;
                22'h210064: lut_coeff = 32'h3E8E39DA;
                22'h210065: lut_coeff = 32'h3EFB14BE;
                22'h210066: lut_coeff = 32'h3DC7C5C2;
                22'h210067: lut_coeff = 32'hBED4DB31;
                22'h210080: lut_coeff = 32'h3EB504F3;
                22'h210081: lut_coeff = 32'hBEB504F3;
                22'h210082: lut_coeff = 32'hBEB504F3;
                22'h210083: lut_coeff = 32'h3EB504F3;
                22'h210084: lut_coeff = 32'h3EB504F3;
                22'h210085: lut_coeff = 32'hBEB504F3;
                22'h210086: lut_coeff = 32'hBEB504F3;
                22'h210087: lut_coeff = 32'h3EB504F3;
                22'h2100A0: lut_coeff = 32'h3E8E39DA;
                22'h2100A1: lut_coeff = 32'hBEFB14BE;
                22'h2100A2: lut_coeff = 32'h3DC7C5C2;
                22'h2100A3: lut_coeff = 32'h3ED4DB31;
                22'h2100A4: lut_coeff = 32'hBED4DB31;
                22'h2100A5: lut_coeff = 32'hBDC7C5C2;
                22'h2100A6: lut_coeff = 32'h3EFB14BE;
                22'h2100A7: lut_coeff = 32'hBE8E39DA;
                22'h2100C0: lut_coeff = 32'h3E43EF15;
                22'h2100C1: lut_coeff = 32'hBEEC835E;
                22'h2100C2: lut_coeff = 32'h3EEC835E;
                22'h2100C3: lut_coeff = 32'hBE43EF15;
                22'h2100C4: lut_coeff = 32'hBE43EF15;
                22'h2100C5: lut_coeff = 32'h3EEC835E;
                22'h2100C6: lut_coeff = 32'hBEEC835E;
                22'h2100C7: lut_coeff = 32'h3E43EF15;
                22'h2100E0: lut_coeff = 32'h3DC7C5C2;
                22'h2100E1: lut_coeff = 32'hBE8E39DA;
                22'h2100E2: lut_coeff = 32'h3ED4DB31;
                22'h2100E3: lut_coeff = 32'hBEFB14BE;
                22'h2100E4: lut_coeff = 32'h3EFB14BE;
                22'h2100E5: lut_coeff = 32'hBED4DB31;
                22'h2100E6: lut_coeff = 32'h3E8E39DA;
                22'h2100E7: lut_coeff = 32'hBDC7C5C2;
                default: lut_coeff = 32'h00000000;
            endcase
        end
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_coeff_0  <= 32'h00000000;
        out_coeff_1  <= 32'h00000000;
        out_coeff_2  <= 32'h00000000;
        out_coeff_3  <= 32'h00000000;
        out_coeff_4  <= 32'h00000000;
        out_coeff_5  <= 32'h00000000;
        out_coeff_6  <= 32'h00000000;
        out_coeff_7  <= 32'h00000000;
        out_coeff_8  <= 32'h00000000;
        out_coeff_9  <= 32'h00000000;
        out_coeff_10 <= 32'h00000000;
        out_coeff_11 <= 32'h00000000;
        out_coeff_12 <= 32'h00000000;
        out_coeff_13 <= 32'h00000000;
        out_coeff_14 <= 32'h00000000;
        out_coeff_15 <= 32'h00000000;
    end else if (input_accepted) begin
        busy         <= 1'b1;
        out_coeff_0  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd0);
        out_coeff_1  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd1);
        out_coeff_2  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd2);
        out_coeff_3  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd3);
        out_coeff_4  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd4);
        out_coeff_5  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd5);
        out_coeff_6  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd6);
        out_coeff_7  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd7);
        out_coeff_8  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd8);
        out_coeff_9  <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd9);
        out_coeff_10 <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd10);
        out_coeff_11 <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd11);
        out_coeff_12 <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd12);
        out_coeff_13 <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd13);
        out_coeff_14 <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd14);
        out_coeff_15 <= lut_coeff(in_n_mfcc, in_n_mels, in_k, in_norm_ortho, 5'd15);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
